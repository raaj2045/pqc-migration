#!/usr/bin/env python3
"""validator_scaling_v2 sweep orchestrator.

For each (N, rate, scheme) triple in the cartesian product of
  N      ∈ {4, 7, 16}
  rate   ∈ {10, 50, 100, 200, 500} tx/s
  scheme ∈ {secp256k1, mldsa44}

we
  1. tear down any existing testnet,
  2. FORCE_REBUILD init the testnet at this N,
  3. start the chain, wait for it to be live,
  4. spawn a CPU sampler thread (docker stats every 5 s),
  5. run loadgen at the target rate for 300 s + 30 s drain,
  6. write per-run JSON to results/N{n}_rate{r}_{scheme}.json,
  7. tear the testnet down.

Pre-signed pools are GENERATED ONCE up front (presigner is fast: ~7 s
for secp, ~16 s for mldsa). Within a run, the chain is fresh so all
sender sequences are 0..K-1; the pool's sequences 0..24999 are reused
across runs.

Hard rules (do not override autonomously):
  - 15 min wall timeout per run
  - 3 consecutive *crashes* (not saturation) → abort the sweep
  - 5 h total budget → stop after the current run, write what we have
  - never overwrite an existing result file (resumable)

A "crash" is: chain failed to reach height ≥ 1, init_testnet.sh failed,
or loadgen subprocess exited non-zero / timed out. A "saturation" run
(p99 > 10 s, committed < 90 %, high HTTP errors from sequence-mismatch
rejections at the chain's ceiling) is DATA, not failure — high error
rates at rate=200/500 are exactly what we are measuring.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths                                                                       #
# --------------------------------------------------------------------------- #
EXP_DIR = Path(__file__).parent.resolve()
COSMOS_DIR = EXP_DIR.parent.parent / "cosmos"
TESTNET_DIR = COSMOS_DIR / "docker" / "testnet"
PRESIGN_DIR = EXP_DIR / "presigned"
RESULTS_DIR = EXP_DIR / "results"
LOG_DIR = EXP_DIR / "logs"
CPU_DIR = EXP_DIR / "cpu_samples"

PRESIGN_BIN = EXP_DIR / "tools" / "presigner_bin"
LOADGEN_BIN = EXP_DIR / "tools" / "loadgen_bin"
INIT_SCRIPT = TESTNET_DIR / "scripts" / "init_testnet.sh"

SECP_ADDRS = PRESIGN_DIR / "secp_addrs.jsonl"
MLDSA_ADDRS = PRESIGN_DIR / "mldsa_addrs.jsonl"
SECP_POOL = PRESIGN_DIR / "secp.jsonl"
MLDSA_POOL = PRESIGN_DIR / "mldsa.jsonl"

RPC_URL = "http://localhost:26657"

# --------------------------------------------------------------------------- #
# Sweep parameters                                                            #
# --------------------------------------------------------------------------- #
NS = [4, 7, 16]
RATES = [10, 50, 100, 200, 500]
SCHEMES = ["secp256k1", "mldsa44"]
DURATION_S = 300
DRAIN_S = 30
PER_RUN_TIMEOUT_S = 15 * 60     # 15 min hard wall
TOTAL_BUDGET_S = 5 * 3600       # 5 h
CHAIN_HEALTH_TIMEOUT_S = 90     # how long to wait for chain to start producing blocks
CPU_SAMPLE_INTERVAL_S = 5
MAX_CONSECUTIVE_CRASHES = 3


# --------------------------------------------------------------------------- #
# Small utilities                                                             #
# --------------------------------------------------------------------------- #
def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def result_path(n: int, rate: int, scheme: str) -> Path:
    return RESULTS_DIR / f"N{n}_rate{rate}_{scheme}.json"


def cpu_path(n: int, rate: int, scheme: str) -> Path:
    return CPU_DIR / f"N{n}_rate{rate}_{scheme}.csv"


def loadgen_log_path(n: int, rate: int, scheme: str) -> Path:
    return LOG_DIR / f"N{n}_rate{rate}_{scheme}.log"


def chain_height() -> int:
    """Return the chain's latest height, or 0 on any error. The poller never
    raises — chain not-yet-up looks the same as a transient error from
    the orchestrator's point of view (we just keep waiting)."""
    try:
        r = subprocess.run(
            ["curl", "-s", "--max-time", "2", f"{RPC_URL}/status"],
            capture_output=True, text=True, timeout=4,
        )
        if r.returncode != 0 or not r.stdout:
            return 0
        doc = json.loads(r.stdout)
        h = doc.get("result", {}).get("sync_info", {}).get("latest_block_height", "0")
        return int(h)
    except Exception:
        return 0


# --------------------------------------------------------------------------- #
# Chain lifecycle                                                             #
# --------------------------------------------------------------------------- #
def docker_compose_down():
    subprocess.run(
        ["docker", "compose", "down"],
        cwd=TESTNET_DIR, capture_output=True, timeout=60,
    )


def init_chain(n: int) -> bool:
    """FORCE_REBUILD init the testnet at this N. Returns True on success."""
    docker_compose_down()
    env = os.environ.copy()
    env["FORCE_REBUILD"] = "1"
    cmd = [
        str(INIT_SCRIPT),
        "--validators", str(n),
        "--key-type", "secp256k1",  # validator delegator algo; consensus stays ed25519
        "--secp-addrs-file", str(SECP_ADDRS),
        "--mldsa-addrs-file", str(MLDSA_ADDRS),
    ]
    log(f"  init_testnet.sh --validators {n}")
    r = subprocess.run(cmd, cwd=TESTNET_DIR, env=env,
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        log(f"  init_testnet failed: {r.stderr[-500:]}")
        return False
    return True


def start_chain() -> bool:
    """docker compose up -d, then poll /status until height ≥ 1 or timeout."""
    r = subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=TESTNET_DIR, capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        log(f"  compose up failed: {r.stderr[-500:]}")
        return False

    deadline = time.time() + CHAIN_HEALTH_TIMEOUT_S
    while time.time() < deadline:
        if chain_height() >= 1:
            return True
        time.sleep(2)
    log(f"  chain failed to produce a block within {CHAIN_HEALTH_TIMEOUT_S}s")
    return False


def stop_chain():
    docker_compose_down()


# --------------------------------------------------------------------------- #
# CPU sampler thread                                                          #
# --------------------------------------------------------------------------- #
class CPUSampler:
    """Samples CPU% for cosmos-testnet-* containers via `docker stats` and
    appends each sample to a CSV. Tracks per-container peak in memory so
    the orchestrator can read it after stop()."""

    def __init__(self, csv_path: Path):
        self.csv_path = csv_path
        self._stop = threading.Event()
        self._thread = None
        self.peaks: dict[str, float] = {}
        self.samples = 0

    def start(self):
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = open(self.csv_path, "w")
        self._fp.write("ts,container,cpu_pct,mem_mb\n")
        self._fp.flush()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            t0 = time.time()
            try:
                r = subprocess.run(
                    ["docker", "stats", "--no-stream", "--format",
                     "{{.Name}} {{.CPUPerc}} {{.MemUsage}}"],
                    capture_output=True, text=True, timeout=8,
                )
                ts = int(time.time())
                if r.returncode == 0:
                    for line in r.stdout.splitlines():
                        parts = line.split(maxsplit=2)
                        if len(parts) < 3:
                            continue
                        name, cpu, mem = parts
                        if "cosmos-testnet-" not in name:
                            continue
                        try:
                            cpu_pct = float(cpu.rstrip("%"))
                        except ValueError:
                            continue
                        # parse "395.1MiB / 1.5GiB" → 395.1
                        mem_mb = 0.0
                        try:
                            mu = mem.split("/")[0].strip()
                            v = float("".join(c for c in mu if c.isdigit() or c == "."))
                            if "GiB" in mu:
                                v *= 1024
                            mem_mb = v
                        except Exception:
                            pass
                        self.peaks[name] = max(self.peaks.get(name, 0.0), cpu_pct)
                        self._fp.write(f"{ts},{name},{cpu_pct},{mem_mb:.1f}\n")
                    self._fp.flush()
                    self.samples += 1
            except Exception:
                pass
            elapsed = time.time() - t0
            wait = max(0.0, CPU_SAMPLE_INTERVAL_S - elapsed)
            self._stop.wait(wait)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        try:
            self._fp.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Run a single (N, rate, scheme) cell                                         #
# --------------------------------------------------------------------------- #
def run_one(n: int, rate: int, scheme: str) -> dict:
    """Returns a dict with at least {status, peak_cpu, has_result_file}.
    status ∈ {ok, saturated, crashed, timeout, skipped}."""

    out_path = result_path(n, rate, scheme)
    log_path = loadgen_log_path(n, rate, scheme)

    if out_path.exists():
        log(f"  result already exists at {out_path.name}, skipping")
        return {"status": "skipped", "has_result_file": True}

    pool = SECP_POOL if scheme == "secp256k1" else MLDSA_POOL

    if not init_chain(n):
        return {"status": "crashed", "reason": "init_failed", "has_result_file": False}
    if not start_chain():
        stop_chain()
        return {"status": "crashed", "reason": "chain_no_block", "has_result_file": False}

    # Settle: a few extra seconds for all 4+ peers to connect and the
    # mempool to be ready to accept.
    time.sleep(3)

    cpu = CPUSampler(cpu_path(n, rate, scheme))
    cpu.start()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(LOADGEN_BIN),
        "--pool", str(pool),
        "--rpc-url", RPC_URL,
        "--rate", str(rate),
        "--duration", str(DURATION_S),
        "--senders", "8",
        "--drain", str(DRAIN_S),
        "--out", str(out_path),
    ]
    log(f"  loadgen --rate {rate} --duration {DURATION_S} (scheme={scheme})")

    status = "ok"
    reason = ""
    try:
        with open(log_path, "w") as logf:
            r = subprocess.run(
                cmd, stdout=logf, stderr=subprocess.STDOUT,
                timeout=PER_RUN_TIMEOUT_S,
            )
            if r.returncode != 0:
                status = "crashed"
                reason = f"loadgen rc={r.returncode}"
    except subprocess.TimeoutExpired:
        status = "timeout"
        reason = f"per-run timeout ({PER_RUN_TIMEOUT_S}s)"
    except Exception as e:
        status = "crashed"
        reason = f"loadgen exception: {e}"
    finally:
        cpu.stop()

    has_file = out_path.exists()

    # Classify saturation vs ok using the produced metrics, when present.
    saturated = False
    summary = {}
    if has_file:
        try:
            doc = json.loads(out_path.read_text())
            submitted = doc.get("submitted", 0)
            committed = doc.get("committed", 0)
            p99 = (doc.get("latency_inclusion_ms") or {}).get("p99", 0)
            http_errs = doc.get("http_errors", 0)
            summary = {
                "submitted": submitted,
                "committed": committed,
                "p99_ms": p99,
                "http_errors": http_errs,
            }
            if submitted > 0 and (committed / max(1, submitted)) < 0.9:
                saturated = True
            if p99 >= 10_000:
                saturated = True
            # http_errors at high rate are sequence-mismatch rejections from
            # the chain's antehandler when the per-sender mempool window
            # fills up — this IS the saturation signal we want to capture,
            # not a crash. We do NOT classify based on http_errs here.
        except Exception as e:
            log(f"  could not parse result for classification: {e}")

    if status == "ok" and saturated:
        status = "saturated"

    stop_chain()

    peak_cpu = max(cpu.peaks.values(), default=0.0)
    log(f"  status={status} {reason} peak_cpu={peak_cpu:.1f}%  {summary}")

    # Annotate the result file with sweep metadata (CPU peak, status).
    if has_file:
        try:
            doc = json.loads(out_path.read_text())
            doc["sweep_meta"] = {
                "n_validators": n,
                "rate": rate,
                "scheme": scheme,
                "duration_s": DURATION_S,
                "drain_s": DRAIN_S,
                "status": status,
                "reason": reason,
                "peak_cpu_pct": peak_cpu,
                "cpu_csv": str(cpu_path(n, rate, scheme).relative_to(EXP_DIR)),
                "log_file": str(log_path.relative_to(EXP_DIR)),
            }
            out_path.write_text(json.dumps(doc, indent=2))
        except Exception as e:
            log(f"  could not annotate result: {e}")

    return {
        "status": status,
        "reason": reason,
        "peak_cpu": peak_cpu,
        "has_result_file": has_file,
        "summary": summary,
    }


# --------------------------------------------------------------------------- #
# Sweep entrypoint                                                            #
# --------------------------------------------------------------------------- #
def regenerate_pool_if_missing():
    """Generate the per-arm presigned pools once if they are not present.
    The presigner is deterministic + fast; safe to skip if already present."""
    SECP_ADDRS.parent.mkdir(parents=True, exist_ok=True)
    if not SECP_ADDRS.exists():
        log("regenerating secp_addrs.jsonl")
        subprocess.run([str(PRESIGN_BIN), "emit-addresses",
                        "--scheme", "secp256k1", "--senders", "8",
                        "--out", str(SECP_ADDRS)], check=True)
    if not MLDSA_ADDRS.exists():
        log("regenerating mldsa_addrs.jsonl")
        subprocess.run([str(PRESIGN_BIN), "emit-addresses",
                        "--scheme", "mldsa44", "--senders", "8",
                        "--out", str(MLDSA_ADDRS)], check=True)
    if not SECP_POOL.exists():
        log("regenerating secp.jsonl pool (~7 s)")
        subprocess.run([str(PRESIGN_BIN), "sign",
                        "--scheme", "secp256k1", "--senders", "8",
                        "--txs-per-sender", "25000",
                        "--chain-id", "testnet",
                        "--account-num-base", "0",
                        "--gas-limit", "250000",
                        "--out", str(SECP_POOL)], check=True)
    if not MLDSA_POOL.exists():
        log("regenerating mldsa.jsonl pool (~16 s)")
        subprocess.run([str(PRESIGN_BIN), "sign",
                        "--scheme", "mldsa44", "--senders", "8",
                        "--txs-per-sender", "25000",
                        "--chain-id", "testnet",
                        "--account-num-base", "8",
                        "--gas-limit", "250000",
                        "--out", str(MLDSA_POOL)], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", default=",".join(map(str, NS)))
    ap.add_argument("--rates", default=",".join(map(str, RATES)))
    ap.add_argument("--schemes", default=",".join(SCHEMES))
    ap.add_argument("--start-from", default=None,
                    help="N,rate,scheme triple to resume from (e.g. '7,100,secp256k1')")
    args = ap.parse_args()

    ns = [int(x) for x in args.ns.split(",")]
    rates = [int(x) for x in args.rates.split(",")]
    schemes = args.schemes.split(",")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    CPU_DIR.mkdir(parents=True, exist_ok=True)

    regenerate_pool_if_missing()

    # Build the run plan. Order: outer N, middle rate, inner scheme. So we
    # do all 10 cells of N=4 first, then N=7, then N=16. This keeps
    # contiguous cells of the same N together for easier mid-sweep audits.
    plan = []
    for n in ns:
        for rate in rates:
            for scheme in schemes:
                plan.append((n, rate, scheme))

    if args.start_from:
        n_s, r_s, s_s = args.start_from.split(",")
        target = (int(n_s), int(r_s), s_s)
        try:
            idx = plan.index(target)
            plan = plan[idx:]
            log(f"resuming at index {idx} ({target})")
        except ValueError:
            log(f"--start-from {args.start_from} not in plan; ignoring")

    log(f"sweep plan: {len(plan)} runs")
    started_at = time.time()
    results = []
    consecutive_crashes = 0

    for i, (n, rate, scheme) in enumerate(plan, 1):
        elapsed = time.time() - started_at
        if elapsed >= TOTAL_BUDGET_S:
            log(f"BUDGET EXHAUSTED ({elapsed/3600:.2f}h ≥ {TOTAL_BUDGET_S/3600}h) — stopping")
            break

        log(f"[{i}/{len(plan)}] N={n} rate={rate} scheme={scheme}  "
            f"elapsed={elapsed/60:.1f}min")
        rec = run_one(n, rate, scheme)
        rec["plan_index"] = i
        rec["n"] = n
        rec["rate"] = rate
        rec["scheme"] = scheme
        results.append(rec)

        # Persist sweep state after every cell so a kill -9 still leaves
        # a usable record of what completed.
        (EXP_DIR / "sweep_state.json").write_text(
            json.dumps({"started_at": started_at,
                        "elapsed_s": time.time() - started_at,
                        "completed": results,
                        "remaining": len(plan) - i},
                       indent=2)
        )

        if rec["status"] == "crashed":
            consecutive_crashes += 1
            if consecutive_crashes >= MAX_CONSECUTIVE_CRASHES:
                log(f"ABORT — {consecutive_crashes} consecutive crashes")
                break
        else:
            consecutive_crashes = 0

    elapsed = time.time() - started_at
    n_ok = sum(1 for r in results if r["status"] == "ok")
    n_sat = sum(1 for r in results if r["status"] == "saturated")
    n_skip = sum(1 for r in results if r["status"] == "skipped")
    n_crash = sum(1 for r in results if r["status"] == "crashed")
    n_to = sum(1 for r in results if r["status"] == "timeout")
    log(f"sweep done: {n_ok} ok, {n_sat} saturated, {n_skip} skipped, "
        f"{n_crash} crashed, {n_to} timeout — elapsed {elapsed/60:.1f}min")


if __name__ == "__main__":
    main()
