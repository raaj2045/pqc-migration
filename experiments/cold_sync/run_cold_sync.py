#!/usr/bin/env python3
"""Cold-sync experiment: time a fresh node catching up to a saturated testnet.

For each scheme in {secp256k1, mldsa44}:
  1. bring up an N-validator testnet with fast block production so we
     accumulate thousands of blocks in minutes, not hours,
  2. wait until node0 reports at least --target-height,
  3. prepare a fresh node home (simd init + genesis copy + peer patching),
  4. launch it as a non-validator container on the testnet's docker network,
  5. poll /status every second for `catching_up`, sample `docker stats` for
     CPU / memory / disk I/O, tear down when caught up (or timeout).

We keep the testnet running throughout the sync so peers can actually serve
blocks. The fresh node joins via persistent_peers (node ID @ container IP).

Results land at `results/<scheme>.json` — one per scheme, replayable because
the orchestrator skips schemes whose file already exists.
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
from typing import Optional
from urllib import request
from urllib.error import URLError, HTTPError


# --------------------------------------------------------------------------- #
# Paths                                                                       #
# --------------------------------------------------------------------------- #
EXP_DIR = Path(__file__).parent.resolve()
ROOT = EXP_DIR.parent.parent
COSMOS_DIR = ROOT / "cosmos"
TESTNET_DIR = COSMOS_DIR / "docker" / "testnet"
SIMD_BIN = COSMOS_DIR / "build" / "simd"

RESULTS_DIR = EXP_DIR / "results"
LOG_DIR = EXP_DIR / "logs"
FRESH_DIR_ROOT = EXP_DIR / "fresh_homes"

# --------------------------------------------------------------------------- #
# Defaults                                                                    #
# --------------------------------------------------------------------------- #
DEFAULT_SCHEMES = ["secp256k1", "mldsa44"]
DEFAULT_VALIDATORS = 4
DEFAULT_COMMIT_TIMEOUT = "1s"       # pack blocks quickly
DEFAULT_TARGET_HEIGHT = 1000        # "saturated" chain for this experiment
DEFAULT_SYNC_TIMEOUT_S = 1800       # hard cap on how long we'll wait to catch up
DEFAULT_HEALTH_TIMEOUT_S = 180

STATS_POLL_S = 2.0
STATUS_POLL_S = 1.0
FRESH_CONTAINER = "cosmos-testnet-sync"
FRESH_IP = "172.28.0.199"           # outside the validator range (.10..+N-1)

# Docker compose names the network "<project>_<name>"; our compose file
# sits in docker/testnet/ so project=testnet and network=testnet_testnet.
TESTNET_NETWORK = "testnet_testnet"


# --------------------------------------------------------------------------- #
# Small utilities                                                             #
# --------------------------------------------------------------------------- #
def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd, *, cwd=None, env=None, check=True, capture=False, timeout=None):
    log(f"$ {' '.join(str(c) for c in cmd)}  (cwd={cwd})")
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None,
        env=env, check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True, timeout=timeout,
    )


def capture(cmd, *, cwd=None) -> str:
    return run(cmd, cwd=cwd, capture=True).stdout.strip()


def _get_json(url, timeout=2.0):
    with request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


# --------------------------------------------------------------------------- #
# Testnet up/down + readiness                                                 #
# --------------------------------------------------------------------------- #
def testnet_up(n: int, scheme: str, commit_timeout: str, health_timeout: int):
    run(["make", "-C", str(TESTNET_DIR), "down"], check=False)
    run(["make", "-C", str(TESTNET_DIR), "up",
         f"N={n}", f"KEY_TYPE={scheme}", f"COMMIT_TIMEOUT={commit_timeout}"])
    run(["make", "-C", str(TESTNET_DIR), "health",
         f"N={n}", f"HEALTH_TIMEOUT={health_timeout}", "HEALTH_MIN_HEIGHT=3"])


def testnet_down():
    run(["make", "-C", str(TESTNET_DIR), "down"], check=False)


def testnet_meta() -> dict:
    return json.loads((TESTNET_DIR / "testnet-data" / ".meta.json").read_text())


def wait_for_height(target: int, timeout_s: int = 1800):
    meta = testnet_meta()
    url = f"http://127.0.0.1:{meta['base_rpc_port']}/status"
    deadline = time.time() + timeout_s
    last_logged = 0
    while time.time() < deadline:
        try:
            j = _get_json(url, timeout=2.0)
            h = int(j["result"]["sync_info"]["latest_block_height"])
            if h - last_logged >= 100:
                log(f"saturation: height={h} / target={target}")
                last_logged = h
            if h >= target:
                return h
        except (URLError, HTTPError, TimeoutError, ValueError, KeyError):
            pass
        time.sleep(2.0)
    raise TimeoutError(f"testnet did not reach height {target} in {timeout_s}s")


# --------------------------------------------------------------------------- #
# Fresh-node home preparation                                                 #
# --------------------------------------------------------------------------- #
def node_id(home: Path) -> str:
    return capture([str(SIMD_BIN), "comet", "show-node-id", "--home", str(home)])


def prepare_fresh_home(scheme: str, n: int) -> Path:
    """Build a fresh node home that block-syncs from the running testnet.

    Returns the path that should be bind-mounted at /cosmos.
    """
    meta = testnet_meta()
    fresh = FRESH_DIR_ROOT / scheme
    if fresh.exists():
        shutil.rmtree(fresh)
    fresh.mkdir(parents=True)

    run([str(SIMD_BIN), "init", "fresh-node",
         "--chain-id", meta["chain_id"], "--home", str(fresh)])

    # Replace the freshly-initialised genesis with the testnet's genesis so
    # the fresh node actually agrees on the initial state.
    src_genesis = TESTNET_DIR / "testnet-data" / "node0" / "simd" / "config" / "genesis.json"
    shutil.copy(src_genesis, fresh / "config" / "genesis.json")

    # Build persistent_peers from every validator's node ID + container IP.
    ip_base = meta["ip_base"]
    ip_start = meta["ip_start"]
    p2p_port = meta["base_p2p_port"]
    peers = []
    for i in range(n):
        home_i = TESTNET_DIR / "testnet-data" / f"node{i}" / "simd"
        nid = node_id(home_i)
        ip = f"{ip_base}{ip_start + i}"
        peers.append(f"{nid}@{ip}:{p2p_port}")
    peer_str = ",".join(peers)

    cfg_path = fresh / "config" / "config.toml"
    _patch_config(cfg_path, peer_str)
    log(f"fresh home prepared at {fresh}")
    log(f"persistent_peers = {peer_str}")
    return fresh


def _patch_config(cfg: Path, peers: str):
    """Set persistent_peers, disable state sync, keep block sync on.

    We use sed-style in-place edits because the config.toml format has
    sections and a proper TOML round-trip would rewrite comments we rely on
    for diffability.
    """
    import re
    txt = cfg.read_text()

    def replace_kv(key: str, value: str, quote=True) -> None:
        nonlocal txt
        pattern = rf'^{re.escape(key)} = .*$'
        qv = f'"{value}"' if quote else value
        new_line = f"{key} = {qv}"
        txt, n = re.subn(pattern, new_line, txt, count=1, flags=re.MULTILINE)
        if n == 0:
            log(f"warn: config key not found: {key}")

    replace_kv("persistent_peers", peers)
    replace_kv("allow_duplicate_ip", "true", quote=False)
    # Block sync default is on; state sync default is off. We set them
    # explicitly so the experiment is insensitive to upstream default flips.
    # Some versions use [blocksync] enable, others [block_sync] enable.
    txt = re.sub(r'^enable = .*$', 'enable = true', txt, count=1, flags=re.MULTILINE)
    # state_sync section: force enable=false (belt-and-braces)
    txt = re.sub(
        r'(\[statesync\][\s\S]*?)^enable = .*$',
        r'\1enable = false',
        txt, count=1, flags=re.MULTILINE,
    )
    cfg.write_text(txt)


# --------------------------------------------------------------------------- #
# Fresh-node container lifecycle                                              #
# --------------------------------------------------------------------------- #
def start_fresh_node(fresh_home: Path, rpc_host_port: int, log_path: Path):
    run(["docker", "rm", "-f", FRESH_CONTAINER], check=False)
    cmd = [
        "docker", "run", "-d", "--name", FRESH_CONTAINER,
        "--network", TESTNET_NETWORK, "--ip", FRESH_IP,
        "-v", f"{fresh_home}:/cosmos",
        "-p", f"{rpc_host_port}:26657",
        "cosmos-testnet:local",
        "start", "--log_level", "info",
    ]
    run(cmd)
    # Kick off a background log-collector so it's easy to debug later.
    with open(log_path, "w") as f:
        subprocess.Popen(
            ["docker", "logs", "-f", FRESH_CONTAINER],
            stdout=f, stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )


def stop_fresh_node():
    run(["docker", "rm", "-f", FRESH_CONTAINER], check=False)


# --------------------------------------------------------------------------- #
# Sync telemetry                                                              #
# --------------------------------------------------------------------------- #
class SyncCollector(threading.Thread):
    """Poll fresh node /status + docker stats while sync is in progress."""

    def __init__(self, rpc_host_port: int):
        super().__init__(daemon=True)
        self.rpc = f"http://127.0.0.1:{rpc_host_port}"
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.status_samples = []
        self.stat_samples = []
        self.caught_up_at_ms: Optional[int] = None
        self.caught_up_at_height: Optional[int] = None
        self.first_status_ms: Optional[int] = None

    def _status_loop(self):
        while not self._stop.is_set():
            try:
                j = _get_json(f"{self.rpc}/status", timeout=2.0)
                si = j["result"]["sync_info"]
                rec = {
                    "ts_ms": int(time.time() * 1000),
                    "height": int(si.get("latest_block_height", 0)),
                    "catching_up": bool(si.get("catching_up", True)),
                    "latest_time": si.get("latest_block_time"),
                }
                with self._lock:
                    self.status_samples.append(rec)
                    if self.first_status_ms is None:
                        self.first_status_ms = rec["ts_ms"]
                    if (not rec["catching_up"]) and self.caught_up_at_ms is None:
                        self.caught_up_at_ms = rec["ts_ms"]
                        self.caught_up_at_height = rec["height"]
            except (URLError, HTTPError, TimeoutError, ValueError, KeyError):
                pass
            self._stop.wait(STATUS_POLL_S)

    def _stats_loop(self):
        while not self._stop.is_set():
            try:
                out = subprocess.run(
                    ["docker", "stats", "--no-stream", "--format", "{{json .}}",
                     FRESH_CONTAINER],
                    capture_output=True, text=True, timeout=5,
                )
                line = out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
                if line:
                    try:
                        js = json.loads(line)
                        js["ts_ms"] = int(time.time() * 1000)
                        with self._lock:
                            self.stat_samples.append(js)
                    except json.JSONDecodeError:
                        pass
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
            self._stop.wait(STATS_POLL_S)

    def run(self):
        # Start both pollers as child threads of this one so .join() is enough.
        t1 = threading.Thread(target=self._status_loop, daemon=True)
        t2 = threading.Thread(target=self._stats_loop, daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()

    def stop(self):
        self._stop.set()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "status_samples": list(self.status_samples),
                "stat_samples": list(self.stat_samples),
                "caught_up_at_ms": self.caught_up_at_ms,
                "caught_up_at_height": self.caught_up_at_height,
                "first_status_ms": self.first_status_ms,
            }


def wait_for_caught_up(collector: SyncCollector, timeout_s: int) -> bool:
    """Return True when caught_up_at_ms is set; False on timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with collector._lock:
            if collector.caught_up_at_ms is not None:
                return True
        time.sleep(2.0)
    return False


# --------------------------------------------------------------------------- #
# Top-level                                                                   #
# --------------------------------------------------------------------------- #
def one_scheme(scheme: str, args) -> dict:
    testnet_up(args.validators, scheme, args.commit_timeout, args.health_timeout)
    try:
        starting_saturation_ms = int(time.time() * 1000)
        tip_height = wait_for_height(args.target_height, timeout_s=args.saturation_timeout)
        saturation_done_ms = int(time.time() * 1000)
        log(f"saturation complete: tip={tip_height} "
            f"in {(saturation_done_ms - starting_saturation_ms)/1000:.1f}s")

        fresh_home = prepare_fresh_home(scheme, args.validators)

        rpc_host_port = 27657  # fresh node: out of the validator-range
        log_path = LOG_DIR / f"fresh_{scheme}.log"
        start_fresh_node(fresh_home, rpc_host_port, log_path)

        collector = SyncCollector(rpc_host_port)
        collector.start()
        t_start_ms = int(time.time() * 1000)
        caught = wait_for_caught_up(collector, timeout_s=args.sync_timeout)
        t_end_ms = int(time.time() * 1000)
        collector.stop()
        collector.join(timeout=10)

        snap = collector.snapshot()
        sync_duration_ms = None
        if snap["caught_up_at_ms"] and snap["first_status_ms"]:
            sync_duration_ms = snap["caught_up_at_ms"] - snap["first_status_ms"]

        return {
            "scheme": scheme,
            "validators": args.validators,
            "commit_timeout": args.commit_timeout,
            "target_height": args.target_height,
            "tip_height_at_saturation": tip_height,
            "caught_up": caught,
            "launched_fresh_at_ms": t_start_ms,
            "ended_at_ms": t_end_ms,
            "sync_duration_ms": sync_duration_ms,
            "telemetry": snap,
        }
    finally:
        stop_fresh_node()
        testnet_down()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schemes", nargs="+", default=DEFAULT_SCHEMES,
                    choices=["secp256k1", "mldsa44"])
    ap.add_argument("--validators", type=int, default=DEFAULT_VALIDATORS)
    ap.add_argument("--commit-timeout", default=DEFAULT_COMMIT_TIMEOUT,
                    help="block commit timeout during saturation")
    ap.add_argument("--target-height", type=int, default=DEFAULT_TARGET_HEIGHT,
                    help="saturation target; sync won't start until tip >= this")
    ap.add_argument("--sync-timeout", type=int, default=DEFAULT_SYNC_TIMEOUT_S,
                    help="hard cap on cold-sync wait")
    ap.add_argument("--saturation-timeout", type=int, default=3600,
                    help="hard cap on saturation phase (seconds)")
    ap.add_argument("--health-timeout", type=int, default=DEFAULT_HEALTH_TIMEOUT_S)
    args = ap.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    FRESH_DIR_ROOT.mkdir(parents=True, exist_ok=True)

    for scheme in args.schemes:
        out = RESULTS_DIR / f"{scheme}.json"
        if out.exists():
            log(f"skip {out.name}: already exists")
            continue
        try:
            rec = one_scheme(scheme, args)
            out.write_text(json.dumps(rec, indent=2, default=str))
            log(f"wrote {out}")
        except Exception as e:
            err = {
                "scheme": scheme,
                "error": repr(e),
                "ended_at_ms": int(time.time() * 1000),
            }
            out.write_text(json.dumps(err, indent=2))
            log(f"FAILED {scheme}: {e}")


if __name__ == "__main__":
    main()
