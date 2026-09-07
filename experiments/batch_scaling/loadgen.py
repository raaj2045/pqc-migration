#!/usr/bin/env python3
"""One cell of the batch-scaling run: submit a group of G transfers, relay
the whole group in one shared light-client update, ack every packet back,
and record how long the group took and what it cost.

This measures the transfer MECHANISM's scaling, not proving time: the
EVM-side light client is required (by check_setup.py, run first) to be bound
to SP1MockVerifier, so the forward leg's proof is free. The real per-proof
cost (~10 min, CPU) was already measured separately — see
../../devnet/README.md#proving and ../migration_throughput/README.md.

Two legs, same asymmetry migration_throughput documents, but the FORWARD leg
is now the batched one:

  FORWARD  (Cosmos -> EVM) all G transfers submitted, then relayed in chunks
           bounded by geth's real tx-size cap (relay_pool.py, see
           find_relay_ceiling.py), each chunk one proof-api request / one
           on-chain multicall, dispatched across a pool of EVM accounts
           concurrently. A group within the chunk size is just one chunk —
           one shared light-client update, same as before chunking existed.
           A larger group may see more than one real update, since chunks
           are dispatched concurrently — see relay_pool.py's module
           docstring and README.md's "Chunked relay: true cost against the
           ideal". Mock-verified: not finality-bound, no real proving.

  RETURN   (EVM -> Cosmos) each packet's ack is submitted via
           devnet/step-ack.js, real cw-ics08-wasm-eth BLS verification. Acks
           submitted before Ethereum finality advances again share ONE
           MsgUpdateClient, exactly as in migration_throughput — this is what
           makes the run finality-bound and gives the ~5-7 minute wall-clock
           floor per group.

"Fully confirmed" (what the total-time measurement is anchored to) means every
packet in the group has been acknowledged back on Cosmos.

Known limitations (see README.md for the full statement):
  - all transfers in a group are simple ICS-20 transfers of the same amount,
    not varied real-world transaction types
  - runs assume nothing else is submitting to the chain concurrently
  - the validator set is not varied
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "devnet" / "lib"))
import config  # noqa: E402
import check_setup  # noqa: E402
import setup_pool  # noqa: E402
import relay_pool  # noqa: E402
import ack_pool  # noqa: E402


@dataclass
class Packet:
    seq: int   # local index within this group's submission loop, 0..n-1
    amount: int
    chain_seq: str = ""   # real on-chain IBC packet sequence, assigned by the chain;
                           # relay-chunk.js's recv-result-<chain_seq>.json is keyed by
                           # this, NOT by `seq` above — the two are unrelated numbers.
    submit_ts: float = 0.0
    commit_ts: float = 0.0
    credited_ts: float = 0.0   # forward leg: destination (EVM) credited this transfer
    ack_ts: float = 0.0        # return leg: full round-trip ack landed back on Cosmos
    window_id: int = -1
    tx_hash: str = ""
    status: str = "pending"   # pending | committed | credited | acked | failed
    error: str = ""
    ack_gas: int = 0
    update_client_gas_return_leg: int = 0   # Cosmos-side MsgUpdateClient (return leg, per window)


@dataclass
class GroupResult:
    group_size: int
    repeat: int
    pool_size: int
    relay_pool_size: int
    relay_concurrency: int
    chunk_size: int
    ack_pool_size: int
    started_at: float
    finished_at: float
    offered: int
    submitted: int
    committed: int
    relayed: int
    acked: int
    failed: int
    status: str              # ok | failed
    failure_kind: str        # "" | gas_limit_exceeded | timeout | reverted | ...
    failure_message: str
    windows_used: int        # return-leg finality windows consumed
    batch_relay: dict        # relay_pool.py's aggregated chunk summary, or {} on failure before that point
    latency_stats: dict = field(default_factory=dict)   # see _compute_latency_stats
    packets: list = field(default_factory=list)

    def to_json(self) -> dict:
        d = asdict(self)
        d["packets"] = [asdict(p) for p in self.packets]
        return d

    @property
    def total_time_s(self) -> float:
        """First submission to every packet acknowledged. Undefined (nan) on failure."""
        acked = [p for p in self.packets if p.status == "acked"]
        if self.status != "ok" or len(acked) != self.group_size:
            return float("nan")
        return max(p.ack_ts for p in acked) - min(p.submit_ts for p in self.packets)


def _percentile(sorted_xs: list[float], p: float) -> float:
    """Linear-interpolation percentile (the usual convention) over an
    already-sorted list. NaN on an empty input.
    """
    if not sorted_xs:
        return float("nan")
    k = (len(sorted_xs) - 1) * p
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return sorted_xs[int(k)]
    return sorted_xs[f] + (sorted_xs[c] - sorted_xs[f]) * (k - f)


def _distribution(values: list[float]) -> dict:
    """mean/median/p95/min/max/n over a list of per-transaction latencies.
    The SPREAD is the point (see README.md's latency section) — batched,
    concurrent processing can give early and late transfers in the same
    group very different waits, which an average alone would hide.
    """
    xs = sorted(v for v in values if v is not None and v > 0)
    if not xs:
        return {"n": 0, "mean": None, "median": None, "p95": None, "min": None, "max": None}
    return {
        "n": len(xs),
        "mean": sum(xs) / len(xs),
        "median": _percentile(xs, 0.5),
        "p95": _percentile(xs, 0.95),
        "min": xs[0],
        "max": xs[-1],
    }


def _compute_latency_stats(packets: list[Packet]) -> dict:
    """Two distributions, kept separate deliberately:

      credit_latency_s   submit -> destination (EVM) credited. Forward-leg
                          only; identical between the sequential-ack and
                          pooled-ack configurations, since ack pooling never
                          touches this leg.
      round_trip_latency_s   submit -> ack landed back on Cosmos. This is
                          the one that differs between configs — pooling the
                          ack step changes how long individual transfers
                          wait for their OWN ack, not just the group's total
                          wall-clock time, and early-vs-late spread within a
                          pool worker's share is exactly what an average
                          would hide. See README.md's "Per-transaction
                          latency" section for how the two configs compare.
    """
    credit = [p.credited_ts - p.submit_ts for p in packets if p.credited_ts and p.submit_ts]
    round_trip = [p.ack_ts - p.submit_ts for p in packets if p.ack_ts and p.submit_ts]
    return {
        "credit_latency_s": _distribution(credit),
        "round_trip_latency_s": _distribution(round_trip),
    }


def _checkpoint_path(out: Path) -> Path:
    return out.with_name(out.stem + ".checkpoint.json")


def _save_checkpoint(path: Path, state: dict):
    # Write-then-rename so a crash mid-write never leaves a half-written,
    # unparseable checkpoint for the next invocation to trip over.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(path)


def _load_checkpoint(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


class Chain:
    def __init__(self, cfg):
        self.cfg = cfg
        self.bin = cfg["PQCHAIND_BIN"]
        self.home = cfg["CHAIN_HOME"]
        self.node = cfg["CHAIN_NODE"]

    def _run(self, args, timeout=120, parse=True):
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"{' '.join(args[:5])}: {r.stderr[-800:] or r.stdout[-800:]}")
        return json.loads(r.stdout) if parse else r.stdout

    def height(self) -> int:
        out = self._run([self.bin, "status", "--node", self.node])
        return int(out["sync_info"]["latest_block_height"])

    def sender_address(self, key_name: str) -> str:
        out = self._run([self.bin, "keys", "show", key_name, "-a",
                         "--home", self.home, "--keyring-backend", "test"],
                        parse=False)
        return out.strip()

    def send_transfer(self, to_client, receiver, amount, denom, sender_key,
                      sender_addr, timeout_ts, workdir: Path) -> str:
        # Same construction as experiments/migration_throughput/loadgen.py —
        # see that file's docstring for why this goes through sendtx.py rather
        # than `pqchaind tx ibc-transfer transfer`.
        msg = {
            "@type": "/ibc.applications.transfer.v1.MsgTransfer",
            "source_port": "transfer",
            "source_channel": to_client,
            "token": {"denom": denom, "amount": str(amount)},
            "sender": sender_addr,
            "receiver": receiver,
            "timeout_height": {"revision_number": "0", "revision_height": "0"},
            "timeout_timestamp": str(timeout_ts),
            "memo": "",
            "encoding": "application/x-solidity-abi",
        }
        msg_path = workdir / f"msg-transfer-{amount}-{timeout_ts}-{time.time_ns()}.json"
        msg_path.write_text(json.dumps(msg, indent=2))
        cmd = self.cfg["SENDTX_CMD"].split() + [str(msg_path), sender_key, "600000"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        msg_path.unlink(missing_ok=True)
        if r.returncode != 0:
            raise RuntimeError(f"sendtx failed: {(r.stderr or r.stdout)[-400:]}")
        out = json.loads(r.stdout.strip().splitlines()[-1])
        if out.get("code", 0) != 0:
            raise RuntimeError(f"tx rejected code={out.get('code')}: {out.get('raw_log', '')[:300]}")
        return out["txhash"]

    def tx_committed(self, tx_hash: str) -> bool:
        try:
            out = self._run([self.bin, "query", "tx", tx_hash,
                             "--node", self.node, "-o", "json"], timeout=30)
            return out.get("code", 1) == 0
        except Exception:
            return False


class Beacon:
    def __init__(self, url):
        self.url = url.rstrip("/")

    def _get(self, path):
        import urllib.request
        with urllib.request.urlopen(f"{self.url}{path}", timeout=15) as r:
            return json.load(r)

    def finalized_epoch(self) -> int:
        d = self._get("/eth/v1/beacon/states/head/finality_checkpoints")["data"]
        return int(d["finalized"]["epoch"])


def submit_group(chain: Chain, cfg, n: int, pool_keys: list[str], log) -> list[Packet]:
    """Submit n transfers, divided evenly across pool_keys as contiguous
    slices of the group's local sequence numbers (0..n-1). Each account
    submits its own slice SEQUENTIALLY (correct account-sequence handling
    within itself — Cosmos rejects a second in-flight tx from an account
    before the first's sequence number is consumed), but every account in
    the pool runs CONCURRENTLY with the others, since they never share a
    sequence number. pool_keys of length 1 is the original single-account
    behavior.
    """
    receiver = cfg["RECEIVER_ADDR"]
    to_client = cfg["COSMOS_CLIENT_ID"]
    denom = cfg.get("LOADGEN_DENOM", "stake")
    amount = int(cfg.get("LOADGEN_AMOUNT", "1000"))
    workdir = Path(cfg["DEVNET_DIR"])

    P = len(pool_keys)
    shares = [n // P + (1 if i < n % P else 0) for i in range(P)]
    starts = [sum(shares[:i]) for i in range(P)]
    addrs = {key: chain.sender_address(key) for key in pool_keys}
    packets: list[Packet | None] = [None] * n
    done = [0]
    lock = threading.Lock()
    log_every = max(1, n // 20)  # ~20 progress lines total, not one per tx at large n

    def submit_share(key, start, count):
        addr = addrs[key]
        t0 = time.time()
        ok = 0
        for j in range(count):
            i = start + j
            pkt = Packet(seq=i, amount=amount)
            pkt.submit_ts = time.time()
            try:
                pkt.tx_hash = chain.send_transfer(
                    to_client, receiver, amount, denom, key, addr,
                    timeout_ts=int(time.time() + 3600), workdir=workdir)
                pkt.status = "submitted"
                ok += 1
            except Exception as e:
                pkt.status = "failed"
                pkt.error = str(e)[:300]
            packets[i] = pkt
            with lock:
                done[0] += 1
                if done[0] % log_every == 0 or done[0] == n:
                    log(f"  SUBMIT PROGRESS: {done[0]}/{n} submitted")
        log(f"  [{key}] submitted {ok}/{count} in {time.time() - t0:.1f}s")

    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=P) as ex:
        futures = [ex.submit(submit_share, key, starts[i], shares[i])
                   for i, key in enumerate(pool_keys) if shares[i] > 0]
        for f in futures:
            f.result()  # surfaces any exception submit_share didn't catch itself
    elapsed = time.time() - t0
    ok = sum(1 for p in packets if p and p.status == "submitted")
    log(f"  group of {n}: submitted {ok}/{n} across {P} account(s) in "
        f"{elapsed:.1f}s ({n / elapsed:.2f}/s achieved)")
    return packets


def await_commits(chain: Chain, packets: list[Packet], log, timeout=300):
    deadline = time.time() + timeout
    pending = [p for p in packets if p.status == "submitted"]
    while pending and time.time() < deadline:
        still = []
        for p in pending:
            if chain.tx_committed(p.tx_hash):
                p.commit_ts = time.time()
                p.status = "committed"
            else:
                still.append(p)
        pending = still
        if pending:
            time.sleep(3)
    for p in pending:
        p.status = "failed"
        p.error = "not committed within timeout"
    log(f"  committed {sum(1 for p in packets if p.status=='committed')}/{len(packets)}")


def await_provable_height(chain: Chain, packets: list[Packet], log, margin=2, timeout=60):
    """proof-api proves a packet against Cosmos state queried at (roughly) its
    commit height; querying that state too soon after commit — before the
    node has advanced a couple more blocks — was observed to occasionally
    return an EMPTY value for the packet's commitment path, which surfaces on
    the EVM side as SP1ICS07Tendermint's MembershipProofValueMismatch (the
    guest program's claimed value vs. nothing found on chain). Small, cheap
    margin: this chain produces blocks in ~1-2s, so this typically costs a
    few seconds once per group, not per packet.
    """
    committed = [p for p in packets if p.status == "committed"]
    if not committed:
        return
    max_commit_height = 0
    for p in committed:
        try:
            r = subprocess.run([chain.bin, "query", "tx", p.tx_hash,
                               "--node", chain.node, "-o", "json"],
                               capture_output=True, text=True, timeout=30)
            h = int(json.loads(r.stdout).get("height", 0))
            max_commit_height = max(max_commit_height, h)
        except Exception:
            pass
    if max_commit_height == 0:
        return
    target = max_commit_height + margin
    deadline = time.time() + timeout
    while chain.height() < target and time.time() < deadline:
        time.sleep(1)
    log(f"  chain at height {chain.height()} (>= {target}, commit height {max_commit_height})")


def _banner(log, text):
    log("=" * 72)
    log(text)
    log("=" * 72)


def run_one(cfg, group_size: int, repeat: int, log, pool_size: int = 1,
            relay_pool_size: int = 1, relay_concurrency: int | None = None,
            chunk_size: int | None = None,
            ack_pool_size: int = 1, checkpoint_path: Path | None = None) -> GroupResult:
    """Checkpointing (see README.md's "Resumability" section for the full
    rationale): the ack phase can run for hours at large group sizes, so it
    is the one phase that checkpoints incrementally (via checkpoint_cb,
    passed to ack_pool.ack_group_pooled) — a crash mid-ack-phase resumes
    with already-acked packets intact, not from zero. Submission and forward
    relay are both single-digit minutes even at group_size=10000, so they
    checkpoint only at phase-end (submit_done / relay_done) and are simply
    redone in full on resume if interrupted mid-phase — a bounded, small
    cost, not worth the complexity of sub-phase resume for those two.

    A checkpoint whose recorded config (group_size, pool sizes, chunk size)
    doesn't match this call's arguments is treated as stale and ignored —
    never resumed into a mismatched run.
    """
    chain = Chain(cfg)
    beacon = Beacon(cfg["BEACON_URL"])

    pool_keys = setup_pool.resolve_pool(cfg, pool_size)
    resolved_chunk_size = chunk_size or relay_pool.default_chunk_size()
    # Prover-dependent: 1 against the real Groth16 prover (queueing proofs
    # ages them toward ProofIsTooOld), higher against the mock prover, where
    # proving is near-instant and serializing chunks buys nothing. See
    # relay_pool.default_relay_concurrency. --relay-concurrency overrides both.
    resolved_relay_concurrency = (
        relay_pool.default_relay_concurrency(cfg, log)
        if relay_concurrency is None else relay_concurrency
    )
    run_config = {
        "group_size": group_size, "pool_size": len(pool_keys),
        "relay_pool_size": relay_pool_size,
        "relay_concurrency": resolved_relay_concurrency,
        "chunk_size": resolved_chunk_size,
        "ack_pool_size": ack_pool_size,
    }

    state = _load_checkpoint(checkpoint_path) if checkpoint_path else None
    if state and state.get("config") != run_config:
        log(f"  checkpoint config mismatch ({state.get('config')} vs {run_config}) — "
            f"ignoring stale checkpoint, starting fresh")
        state = None

    if state:
        started = state["started_at"]
        packets = [Packet(**d) for d in state["packets"]]
        submit_done = state.get("submit_done", False)
        relay_done = state.get("relay_done", False)
        batch_relay = state.get("batch_relay", {})
        already_acked = sum(1 for p in packets if p.status == "acked")
        log(f"  RESUMING from checkpoint {checkpoint_path.name}: submit_done={submit_done} "
            f"relay_done={relay_done} ({already_acked}/{group_size} already acked)")
    else:
        started = time.time()
        packets = None
        submit_done = relay_done = False
        batch_relay = {}

    def checkpoint(pkts, **extra):
        if not checkpoint_path:
            return
        st = {"config": run_config, "started_at": started,
              "packets": [asdict(p) for p in pkts],
              "submit_done": submit_done, "relay_done": relay_done,
              "batch_relay": batch_relay}
        st.update(extra)
        _save_checkpoint(checkpoint_path, st)

    if not submit_done:
        _banner(log, f"PHASE 1/3: SUBMISSION — group_size={group_size} repeat={repeat} "
                     f"pool={len(pool_keys)}")
        packets = submit_group(chain, cfg, group_size, pool_keys, log)
        await_commits(chain, packets, log)
        await_provable_height(chain, packets, log)
        submit_done = True
        checkpoint(packets)
    else:
        _banner(log, "PHASE 1/3: SUBMISSION — already complete (resumed from checkpoint)")

    out_dir = Path(cfg["DEVNET_DIR"]) / "batch-scaling-relay"
    if not relay_done:
        for f in out_dir.glob("*.json") if out_dir.exists() else []:
            f.unlink()
        _banner(log, f"PHASE 2/3: FORWARD RELAY — pool={relay_pool_size} EVM account(s), "
                     f"concurrency {resolved_relay_concurrency}, chunk size {resolved_chunk_size}")
        failure_kind, failure_message, batch_relay = relay_pool.relay_group_pooled(
            cfg, packets, resolved_chunk_size, relay_pool_size, log, out_dir,
            concurrency=resolved_relay_concurrency)
        relay_done = not failure_kind
        checkpoint(packets)
    else:
        failure_kind, failure_message = "", ""
        _banner(log, "PHASE 2/3: FORWARD RELAY — already complete (resumed from checkpoint)")

    windows = 0
    if not failure_kind:
        _banner(log, f"PHASE 3/3: RETURN ACK — pool={ack_pool_size} Cosmos account(s)")
        windows = ack_pool.ack_group_pooled(
            cfg, packets, beacon, ack_pool_size, log, out_dir,
            checkpoint_cb=lambda: checkpoint(packets))
        # A packet that never got acked in this leg is a failure too, even
        # though the batch relay itself succeeded.
        for p in packets:
            if p.status not in ("acked", "failed"):
                p.status = "failed"
                p.error = p.error or "not acknowledged within window budget"

    n_failed = sum(1 for p in packets if p.status == "failed")
    if failure_kind:
        status = "failed"
    elif n_failed > 0:
        status = "failed"
        failure_kind = failure_kind or "partial_ack_failure"
        failure_message = failure_message or f"{n_failed}/{group_size} packets never acked"
    else:
        status = "ok"

    finished = time.time()
    result = GroupResult(
        group_size=group_size, repeat=repeat, pool_size=len(pool_keys),
        relay_pool_size=relay_pool_size, relay_concurrency=resolved_relay_concurrency,
        chunk_size=resolved_chunk_size, ack_pool_size=ack_pool_size,
        started_at=started, finished_at=finished,
        offered=len(packets),
        submitted=sum(1 for p in packets if p.status != "failed" or p.tx_hash),
        committed=sum(1 for p in packets if p.status in ("committed", "credited", "acked")),
        relayed=sum(1 for p in packets if p.status in ("credited", "acked")),
        acked=sum(1 for p in packets if p.status == "acked"),
        failed=n_failed,
        status=status, failure_kind=failure_kind, failure_message=str(failure_message)[:500],
        windows_used=windows, batch_relay=batch_relay,
        latency_stats=_compute_latency_stats(packets), packets=packets,
    )
    _log_summary(log, result)
    if checkpoint_path and checkpoint_path.exists():
        checkpoint_path.unlink()
        log(f"  removed checkpoint {checkpoint_path.name} (run complete)")
    return result


def _fmt_dist(d: dict) -> str:
    if not d or not d.get("n"):
        return "n=0"
    return (f"n={d['n']} mean={d['mean']:.1f}s median={d['median']:.1f}s "
            f"p95={d['p95']:.1f}s min={d['min']:.1f}s max={d['max']:.1f}s")


def _log_summary(log, result: "GroupResult"):
    br = result.batch_relay or {}
    forward_gas = int(br.get("totalGas", 0) or 0)
    return_update_gas = sum(p.update_client_gas_return_leg for p in result.packets)
    return_ack_gas = sum(p.ack_gas for p in result.packets)
    total_gas = forward_gas + return_update_gas + return_ack_gas
    n_return_updates = sum(1 for p in result.packets if p.update_client_gas_return_leg)
    total_time = result.total_time_s

    _banner(log, "SUMMARY")
    log(f"  status: {result.status}" +
        (f"  FAILURE: {result.failure_kind}: {result.failure_message[:200]}" if result.status == "failed" else ""))
    log(f"  acked: {result.acked}/{result.offered}")
    if total_time == total_time:  # not NaN
        log(f"  total time: {total_time:.1f}s")
    ls = result.latency_stats or {}
    log(f"  credit latency (submit -> destination credited):  {_fmt_dist(ls.get('credit_latency_s', {}))}")
    log(f"  round-trip latency (submit -> ack):                {_fmt_dist(ls.get('round_trip_latency_s', {}))}")
    log(f"  forward leg: gas {forward_gas}  ({br.get('numChunks', '?')} chunk(s), "
        f"{br.get('numUpdateClientCalls', '?')} real light-client update(s) — TRUE count)")
    log(f"  return leg:  ack gas {return_ack_gas}  update-client gas {return_update_gas} "
        f"({n_return_updates} real light-client update(s) — TRUE count)")
    log(f"  TOTAL gas (all legs): {total_gas}")
    if result.acked > 0:
        log(f"  TRUE gas per transfer: {total_gas / result.acked:.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group-size", type=int, required=True,
                    help="transfers submitted into one shared light-client update")
    ap.add_argument("--pool-size", type=int, default=1,
                    help="number of relayer accounts submitting concurrently, each its "
                         "share of --group-size sequentially. Default 1 (single account, "
                         "the original behavior). >1 requires setup_pool.py to have been "
                         "run first with a matching or larger --pool-size.")
    ap.add_argument("--relay-pool-size", type=int, default=1,
                    help="number of EVM accounts relaying chunks concurrently, each its "
                         "share of the group's chunks sequentially. Default 1. >1 requires "
                         "'node setup-evm-pool.js <N>' to have been run first.")
    ap.add_argument("--relay-concurrency", type=int, default=None,
                    help="max chunks proven/submitted at once, capped by --relay-pool-size. "
                         "Default depends on the prover the EVM light client is bound to: "
                         f"{relay_pool.DEFAULT_RELAY_CONCURRENCY_MOCK} against the mock verifier "
                         "(proving is near-instant, so serializing chunks buys nothing), "
                         f"{relay_pool.DEFAULT_RELAY_CONCURRENCY_REAL} against the real Groth16 "
                         "verifier, where proof generation rather than EVM inclusion is the "
                         "bottleneck: dispatching more chunks than the prover runs in parallel "
                         "just queues them, and queue time is added to every proof's age against "
                         "the light client's 30-minute ProofIsTooOld limit. Raise the real-prover "
                         "case only after measuring real prover parallelism.")
    ap.add_argument("--chunk-size", type=int, default=None,
                    help="max packets per relay transaction. Default: results/relay_ceiling.json's "
                         "measured ceiling minus a safety margin (find_relay_ceiling.py) — "
                         "a group larger than the real per-tx ceiling cannot fit in one "
                         "transaction regardless of this value's setting.")
    ap.add_argument("--ack-pool-size", type=int, default=1,
                    help="number of Cosmos accounts submitting return-leg acks concurrently, "
                         "each its share of outstanding acks sequentially. Default 1 (the "
                         "original fully-sequential behavior). >1 reuses the same account "
                         "pool as --pool-size (setup_pool.py) and may be a different size.")
    ap.add_argument("--repeat", type=int, default=0)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--skip-setup-check", action="store_true",
                    help="skip the SP1MockVerifier/devnet-up precondition check "
                         "(run_sweep.py already checked it once for the whole sweep)")
    args = ap.parse_args()

    cfg = config.require(
        config.load(),
        "PQCHAIND_BIN", "CHAIN_HOME", "CHAIN_ID", "CHAIN_NODE",
        "BEACON_URL", "COSMOS_CLIENT_ID", "RECEIVER_ADDR", "UPDATE_CLIENT_CMD",
        "SENDTX_CMD", "ACK_CMD", "DEVNET_DIR", "SP1_ICS07", "SP1_VERIFIER_MOCK",
        "GETH_RPC",
    )

    def log(msg):
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    if not args.skip_setup_check:
        try:
            check_setup.run_all(cfg, log=log)
        except check_setup.SetupError as e:
            log(f"SETUP CHECK FAILED: {e}")
            sys.exit(1)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    res = run_one(cfg, args.group_size, args.repeat, log, pool_size=args.pool_size,
                  relay_pool_size=args.relay_pool_size,
                  relay_concurrency=args.relay_concurrency, chunk_size=args.chunk_size,
                  ack_pool_size=args.ack_pool_size, checkpoint_path=_checkpoint_path(args.out))
    args.out.write_text(json.dumps(res.to_json(), indent=2))
    log(f"wrote {args.out}  status={res.status} acked={res.acked}/{res.offered} "
        f"windows={res.windows_used}"
        + (f"  FAILURE: {res.failure_kind}: {res.failure_message[:200]}" if res.status == "failed" else ""))
    sys.exit(0 if res.status == "ok" else 1)


if __name__ == "__main__":
    main()
