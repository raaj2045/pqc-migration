"""Pool-concurrent return-leg ack submission — the same pool-concurrency
model as Cosmos submission (loadgen.py's submit_group) and the forward-leg
relay (relay_pool.py), applied to the one piece of this harness that was
still fully sequential: acking.

Why this leg needed pooling too: the forward-leg tx-size wall (see
find_relay_ceiling.py) forced chunking there, but the return leg has no such
wall — MsgAcknowledgement is submitted one packet at a time regardless of
group size, so its cost is pure sequential count. At small group sizes this
finishes inside one Ethereum finality epoch (~6 min) and is cheap. At larger
sizes (observed directly at group=250: 250 sequential acks, ~35 min) it
becomes the dominant wall-clock cost, AND it can span more than one real
finality epoch, forcing multiple genuine MsgUpdateClient calls where an
idealized model would assume one. Pooling this the same way as the other two
legs — reuse the existing Cosmos account pool (setup_pool.py), divide
outstanding acks into contiguous per-account shares, each account acks its
share SEQUENTIALLY (correct nonce handling within itself) while every
account runs CONCURRENTLY — cuts the wall-clock time roughly by the pool
size.

Honest accounting, unchanged mechanism: whether a given ack's
MsgUpdateClient submission actually happened (as opposed to
update-eth-client.py deciding it was a no-op because the client is already
current) was ALREADY ground truth before this module existed, not an
assumption — step-ack.js's stdout carries either one sendtx.py JSON result
(ack only) or two (update then ack), and this module's _gas_from_stdout (a
copy of what loadgen.py used before this module existed) reads that
directly from each packet's own real subprocess output. Unlike the
forward leg (where a separate clientState()-diff check was needed because
proof-api fuses updates invisibly into recvPacket's calldata), the Cosmos
MsgUpdateClient is always its own explicit top-level message with its own
printed result — there was never anything to fuse or misdetect here. Pooling
preserves this per-packet signal unchanged: each pool worker's step-ack.js
subprocess call is independent, so its stdout is still read directly, per
packet, regardless of which account or how many workers ran concurrently.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import concurrent.futures
from pathlib import Path

import setup_pool


def _gas_from_stdout(text):
    """Every gas_used value in step-ack.js's stdout, in order — one sendtx.py
    JSON result per Cosmos tx it broadcasts. Two means the light client
    needed updating first (MsgUpdateClient then MsgAcknowledgement); this is
    the ground-truth signal this module relies on (see module docstring).
    """
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{") or "gas_used" not in line:
            continue
        try:
            out.append(int(json.loads(line)["gas_used"]))
        except (ValueError, KeyError, TypeError):
            continue
    return out


def ack_group_pooled(cfg, packets, beacon, ack_pool_size: int, log, out_dir: Path,
                      max_windows: int = 6, checkpoint_cb=None) -> int:
    """Same window-batching contract as loadgen.py's original sequential ack
    loop (since folded into this module): returns
    the number of windows used. Within each window, outstanding packets are
    divided across the ack pool and dispatched concurrently instead of one
    at a time.

    checkpoint_cb, if given, is called periodically (a fixed number of times
    per window, plus always at window end) so a crash during a multi-hour
    ack phase resumes with already-acked packets intact instead of from
    zero — this is the one phase where that matters (see loadgen.py's
    run_one docstring on why submission/relay don't need the same).
    """
    ack_cmd = cfg.get("ACK_CMD")
    if not ack_cmd:
        log("  ACK_CMD not configured — cannot ack")
        return 0

    pool_keys = setup_pool.resolve_pool(cfg, ack_pool_size)
    log(f"  ack pool: {len(pool_keys)} account(s)")

    windows = 0
    for w in range(max_windows):
        outstanding = [p for p in packets if p.status == "credited"]
        if not outstanding:
            break
        before_epoch = beacon.finalized_epoch()
        windows += 1
        log(f"  window {w}: acking {len(outstanding)} packets across "
            f"{len(pool_keys)} account(s) (finalized epoch {before_epoch})")

        W = len(pool_keys)
        shares = [len(outstanding) // W + (1 if i < len(outstanding) % W else 0) for i in range(W)]
        starts = [sum(shares[:i]) for i in range(W)]
        checkpoint_every = max(1, len(outstanding) // 20)  # ~20 checkpoint writes per window

        done_count = [0]
        update_count = [0]
        lock = threading.Lock()
        total = len(outstanding)

        def ack_one(p, key):
            recv_path = out_dir / f"recv-result-{p.chain_seq}.json"
            try:
                r = subprocess.run(ack_cmd.split() + [str(recv_path), f"--signer-key={key}"],
                                    check=True, capture_output=True, text=True, timeout=2400)
                p.ack_ts = time.time()
                p.window_id = w
                p.status = "acked"
                gas = _gas_from_stdout(r.stdout)
                updated = len(gas) >= 2
                if updated:
                    p.update_client_gas_return_leg, p.ack_gas = gas[0], gas[-1]
                elif gas:
                    p.ack_gas = gas[0]
                return updated
            except subprocess.CalledProcessError as e:
                p.error = ((e.stderr or e.stdout) or "")[-300:]
                log(f"  [{key}] ack seq={p.seq} deferred: {p.error[:140]}")
                return False
            except subprocess.TimeoutExpired:
                p.error = "step-ack.js timed out"
                log(f"  [{key}] ack seq={p.seq} deferred: timeout")
                return False

        def worker(key, start, count):
            t0 = time.time()
            n_ok = 0
            for j in range(count):
                p = outstanding[start + j]
                updated = ack_one(p, key)
                if p.status == "acked":
                    n_ok += 1
                do_checkpoint = False
                with lock:
                    done_count[0] += 1
                    if updated:
                        update_count[0] += 1
                    tag = " [LIGHT-CLIENT UPDATE]" if updated else ""
                    log(f"  [{key}] acked seq={p.seq} ({j + 1}/{count} for this account, "
                        f"{done_count[0]}/{total} overall){tag}")
                    if checkpoint_cb and done_count[0] % checkpoint_every == 0:
                        do_checkpoint = True
                if do_checkpoint:
                    checkpoint_cb()
            log(f"  [{key}] finished its ack share: {n_ok}/{count} in {time.time() - t0:.1f}s")

        t0 = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=W) as ex:
            futures = [ex.submit(worker, pool_keys[i], starts[i], shares[i])
                       for i in range(W) if shares[i] > 0]
            for f in futures:
                f.result()
        elapsed = time.time() - t0
        if checkpoint_cb:
            checkpoint_cb()  # always checkpoint at window end, regardless of cadence

        after_epoch = beacon.finalized_epoch()
        log(f"  window {w}: acked {sum(1 for p in packets if p.status == 'acked')}/{len(packets)} "
            f"in {elapsed:.1f}s (epoch {before_epoch} -> {after_epoch}, "
            f"{update_count[0]} real light-client update(s) this window)")
    return windows
