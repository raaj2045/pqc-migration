"""Chunked, pool-concurrent forward-leg relay.

A group larger than the empirically-measured per-tx packet ceiling (see
find_relay_ceiling.py; 66 packets under go-ethereum's 128KB default tx-size
cap, as of the last run — results/relay_ceiling.json is the source of
truth) cannot be relayed in one EVM transaction at all. This module splits a
group into chunks that individually fit, then relays them using the same
pool-concurrency model already used for Cosmos submission (loadgen.py's
submit_group): the chunk list is divided into contiguous shares across a
pool of EVM accounts (setup-evm-pool.js), each account processing its share
of chunks SEQUENTIALLY (one relay-chunk.js call at a time — correct EVM
nonce handling within itself), while every account in the pool runs
CONCURRENTLY with the others.

Concurrency here is bounded by the PROVER, not by the account pool — so how
much of it is safe depends on WHICH prover is running. Chunks are pulled off
a shared queue by `concurrency` workers, each owning a distinct EVM account
(a nonce sequence to itself), and the default comes from the prover/verifier
pair actually bound on-chain (see default_relay_concurrency()):

  real (Groth16) prover — concurrency 1. Every chunk needs its own proof-api
  request and a chunk's wall time is dominated by SP1 proving, so dispatching
  more chunks than the prover runs in parallel just queues them. That costs
  throughput AND correctness: an SP1 proof carries the timestamp it was built
  at, SP1ICS07Tendermint rejects a proof older than ALLOWED_SP1_CLOCK_DRIFT
  (30 minutes) with ProofIsTooOld, and queue depth is added directly to every
  proof's age — over-dispatching manufactures a staleness failure that
  sequential dispatch cannot hit.

  mock prover — concurrency DEFAULT_RELAY_CONCURRENCY_MOCK. Mock proving is
  near-instant, so no queue forms and no proof ages; the 1-wide default is
  then pure serialization with nothing on the other side of the trade. It
  showed up as per-transfer latency tripling from N=250 to N=1000 (143s ->
  459s median) even while aggregate throughput improved: chunks were waiting
  on each other, not on work.

Honest accounting, not an idealized one: whether chunks amortize a single
light-client update depends on this concurrency. Run sequentially, chunk 2
sees chunk 1's already-updated client and skips re-updating; run
concurrently, each in-flight chunk builds its request before any other's
update has landed, so several carry their own update. This module counts
what actually happened (relay-chunk.js reads the light client's own
latestHeight across each chunk's block — ground truth, not a guess) rather
than assuming amortization the dispatch pattern did not deliver. See
README.md's "Chunked relay: true vs idealized amortization" section.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import concurrent.futures
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CEILING = 66

# How many chunks may be in flight at the prover at once. There is no single
# right answer: the safe value is a property of the PROVER, so the default is
# resolved per-run by default_relay_concurrency() below, and --relay-concurrency
# overrides it in either mode.

# REAL prover: 1, fully sequential, and the arithmetic is not close. DO NOT
# RAISE THIS without re-doing it. Real Groth16 proving costs ~10 min/proof on
# this host (see devnet/README.md#proving and bring-up-native-asset.sh's
# --verifier help), while SP1ICS07Tendermint rejects any proof older than
# ALLOWED_SP1_CLOCK_DRIFT = 30 minutes with ProofIsTooOld. A proof's age on
# arrival is (queue wait + prove time), so the whole staleness budget is barely
# three prove-times deep:
#
#   concurrency 1 -> ~10 min old on arrival   (ok, 3x margin)
#   concurrency 2 -> ~20 min                   (1.5x margin)
#   concurrency 3 -> ~30 min                   (at the wall — rejected)
#
# Over-dispatching buys no throughput from a serial prover; it converts latency
# straight into staleness. Measured: a G=1000 run dispatched 17 chunks at once
# and got ONE proof back in 19.5 minutes, with the other 16 still running when
# the run was killed — the chunks behind it would have aged past 30 minutes and
# reverted with ProofIsTooOld even had they proven. Raise it only after
# measuring that the prover genuinely runs N proofs in parallel WITHOUT
# per-proof time degrading proportionally; if it degrades, that is queueing,
# not parallelism, and the staleness arithmetic above applies unchanged.
DEFAULT_RELAY_CONCURRENCY_REAL = 1

# MOCK prover: 8. Mock proving returns empty proof bytes essentially
# immediately, so the ProofIsTooOld arithmetic above has no purchase — nothing
# queues at the prover and nothing ages. The binding constraint becomes EVM
# inclusion, and each in-flight chunk needs one pool account for its own nonce
# sequence, so this is also bounded by --relay-pool-size (see the clamp in
# relay_group_pooled).
#
# Why 8 and not "as many as there are accounts":
#   - It covers the sizes this experiment actually runs. At the measured chunk
#     size (~60 packets) G=1000 is 17 chunks, so 8 workers halve the chunk
#     phase's critical path; going wider mostly adds accounts, not speed.
#   - Concurrency is not free even with a mock prover: chunks that fly
#     together each build their request before any other's light-client update
#     has landed, so each carries its own MsgUpdateClient instead of
#     amortizing one (see this module's amortization note). Wider dispatch
#     buys latency with gas. 8 keeps that cost bounded and, importantly,
#     honestly counted rather than assumed away.
#   - It stays inside the per-chunk 1800s subprocess timeout with room to
#     spare, and inside typical relay pool sizes, so the default rarely needs
#     the clamp to rescue it.
# Measure before raising: if per-chunk wall time grows with the worker count,
# the bottleneck has moved to the EVM (or to geth's txpool), not away from it.
DEFAULT_RELAY_CONCURRENCY_MOCK = 8

SAFETY_MARGIN = 6  # packets held back from the measured ceiling, guarding against
                    # small per-packet size variance (see find_relay_ceiling.py)


def default_relay_concurrency(cfg, log=None) -> int:
    """Default chunk concurrency for the prover/verifier pair now bound.

    Reads the verifier the EVM light client is actually bound to on-chain
    (check_setup.bound_verifier_kind — one eth_call) rather than trusting a
    config flag, since the whole point of check_setup is that the on-disk
    config and the live devnet can disagree. If that lookup fails for any
    reason we fall back to the conservative real-prover value: guessing "mock"
    wrongly would dispatch 8 real proofs at a 30-minute staleness wall, while
    guessing "real" wrongly only costs speed.
    """
    try:
        import check_setup
        kind, _addr = check_setup.bound_verifier_kind(cfg)
    except Exception as e:  # unreachable RPC, missing cfg key, unknown verifier
        if log:
            log(f"  could not determine the bound verifier ({e}) — defaulting relay "
                f"concurrency to {DEFAULT_RELAY_CONCURRENCY_REAL} (the safe value)")
        return DEFAULT_RELAY_CONCURRENCY_REAL
    if kind == "mock":
        return DEFAULT_RELAY_CONCURRENCY_MOCK
    return DEFAULT_RELAY_CONCURRENCY_REAL


def default_chunk_size() -> int:
    ceiling_file = HERE / "results" / "relay_ceiling.json"
    if ceiling_file.exists():
        ceiling = json.loads(ceiling_file.read_text())["ceiling"]
    else:
        ceiling = DEFAULT_CEILING
    return max(1, ceiling - SAFETY_MARGIN)


def load_evm_pool(cfg) -> list[dict]:
    pool_file = Path(cfg["DEVNET_DIR"]) / "evm-relay-pool.json"
    if not pool_file.exists():
        return []
    return json.loads(pool_file.read_text())


def chunk_hashes(tx_hashes: list[str], chunk_size: int) -> list[list[str]]:
    return [tx_hashes[i:i + chunk_size] for i in range(0, len(tx_hashes), chunk_size)]


def relay_group_pooled(cfg, packets, chunk_size: int, pool_size: int, log,
                        out_root: Path,
                        concurrency: int | None = None) -> tuple[str, str, dict]:
    """Chunk + pool-relay every committed packet.

    Returns (failure_kind, failure_message, aggregated_relay_dict), mirroring
    loadgen.relay_group's contract so run_one() doesn't need to know which
    path was used. failure_kind is "" if every chunk succeeded; a chunk
    failure marks only that chunk's packets failed (partial credit), same
    as the rest of this harness's "record the failure, don't pretend it
    didn't happen" convention — an overall failure_kind is still returned so
    the caller can classify the run, but successfully-relayed chunks' packets
    remain credited.

    `concurrency` caps how many chunks are proven/submitted at once,
    defaulting to default_relay_concurrency(cfg) — prover-dependent, see the
    module docstring. It is clamped to the number of available EVM accounts,
    since an in-flight chunk needs one to itself.
    """
    committed = [p for p in packets if p.status == "committed"]
    if not committed:
        return "no_committed_packets", "nothing committed to relay", {}

    tx_hashes = [p.tx_hash for p in committed]
    chunks = chunk_hashes(tx_hashes, chunk_size)
    log(f"  splitting {len(committed)} packets into {len(chunks)} chunks of <= {chunk_size} "
        f"(measured ceiling: see results/relay_ceiling.json)")
    # Per-packet credited_ts needs the ACTUAL completion time of the specific
    # chunk carrying it, not a single "the whole relay phase finished" time —
    # chunks land at different real times, and collapsing that away would
    # erase exactly the early-vs-late spread per-transaction latency tracking
    # exists to show. See README.md's "Per-transaction latency" section.
    tx_hash_to_chunk_idx = {h: idx for idx, chunk in enumerate(chunks) for h in chunk}
    chunk_finish_ts: list[float | None] = [None] * len(chunks)

    if pool_size < 1:
        pool_size = 1
    pool = load_evm_pool(cfg)
    if len(pool) < pool_size:
        return ("evm_pool_missing",
                f"only {len(pool)} EVM pool account(s) available, need {pool_size} — run "
                f"'node setup-evm-pool.js {pool_size}' first", {})

    # An in-flight chunk needs an EVM account to itself (independent nonce
    # sequence), so concurrency can never exceed the pool — but it is capped
    # by the prover first, and there is no point holding more accounts than
    # chunks either.
    if concurrency is None:
        concurrency = default_relay_concurrency(cfg, log)
    W = max(1, min(concurrency, pool_size, len(chunks)))
    workers = pool[:W]
    if W < min(pool_size, len(chunks)):
        reason = (
            "proof generation, not EVM inclusion, is the bottleneck; queueing "
            "chunks at the prover only ages their proofs toward the 30-minute "
            "ProofIsTooOld limit"
            if concurrency <= DEFAULT_RELAY_CONCURRENCY_REAL else
            "each in-flight chunk needs an EVM account of its own for an "
            "independent nonce sequence"
        )
        log(f"  relay concurrency capped at {W} (requested {concurrency}, pool has "
            f"{pool_size} account(s), {len(chunks)} chunk(s)) — {reason}")

    results: list[dict | None] = [None] * len(chunks)
    errors: list[str | None] = [None] * len(chunks)
    done = [0]
    lock = threading.Lock()

    def run_chunk(idx: int, chunk: list[str], signer_key: str):
        # out_root is shared across all concurrently-running chunks — safe,
        # since recv-result-<chain_seq>.json filenames never collide across
        # chunks (chain sequences are globally unique) — but each chunk's
        # summary file needs its own name.
        summary_name = f"chunk-relay-{idx}.json"
        (out_root / summary_name).unlink(missing_ok=True)
        cmd = ["node", str(HERE / "relay-chunk.js"), str(out_root),
               f"--signer-key={signer_key}", f"--summary={summary_name}"] + chunk
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            errors[idx] = "relay-chunk.js timed out"
            return
        if r.returncode != 0:
            kind, msg = "relay_error", (r.stderr or r.stdout)[-600:]
            for line in (r.stderr or "").splitlines():
                if line.startswith("RELAY_CHUNK_FAILURE"):
                    rest = line[len("RELAY_CHUNK_FAILURE "):]
                    if rest.startswith("kind="):
                        rest2 = rest[len("kind="):]
                        kind, _, msgpart = rest2.partition(" message=")
                        try:
                            msg = json.loads(msgpart)
                        except (ValueError, json.JSONDecodeError):
                            msg = msgpart
            errors[idx] = f"{kind}: {msg}"[:400]
            return
        summary_path = out_root / summary_name
        results[idx] = json.loads(summary_path.read_text()) if summary_path.exists() else None
        if results[idx] is not None:
            chunk_finish_ts[idx] = time.time()

    # A shared queue rather than pre-assigned contiguous shares: chunk prove
    # times vary (a partial trailing chunk proves faster than a full one), so
    # a fixed split leaves accounts idle while others still have work. Pulling
    # on demand also means the number of chunks in flight is exactly W at all
    # times, which is the property the prover cares about.
    next_idx = [0]
    handled = [0] * W

    def take() -> int | None:
        with lock:
            if next_idx[0] >= len(chunks):
                return None
            idx = next_idx[0]
            next_idx[0] += 1
            return idx

    def worker(worker_idx: int):
        entry = workers[worker_idx]
        tag = entry["address"][:10] + "..."
        t0 = time.time()
        while True:
            idx = take()
            if idx is None:
                break
            chunk_t0 = time.time()
            run_chunk(idx, chunks[idx], entry["privateKey"])
            handled[worker_idx] += 1
            res = results[idx]
            with lock:
                done[0] += 1
                took = time.time() - chunk_t0
                if res is not None:
                    upd = " [LIGHT-CLIENT UPDATE]" if res.get("updateIncluded") else ""
                    log(f"  [{tag}] chunk {idx + 1}/{len(chunks)} relayed in {took:.1f}s: "
                        f"{res.get('groupSize', '?')} packets, gas {res.get('totalGas', '?')}, "
                        f"prove {res.get('proveSeconds', '?')}s "
                        f"({done[0]}/{len(chunks)} chunks overall){upd}")
                else:
                    log(f"  [{tag}] chunk {idx + 1}/{len(chunks)} FAILED after {took:.1f}s: "
                        f"{errors[idx]} ({done[0]}/{len(chunks)} chunks overall)")
        log(f"  [{tag}] finished: {handled[worker_idx]} chunk(s) in {time.time() - t0:.1f}s")

    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=W) as ex:
        futures = [ex.submit(worker, i) for i in range(W)]
        for f in futures:
            f.result()
    elapsed = time.time() - t0

    by_hash_to_seq: dict[str, str] = {}
    total_gas = 0
    num_updates = 0
    num_recv = 0
    failed_chunks = []
    for i, res in enumerate(results):
        if res is None:
            failed_chunks.append((i, errors[i] or "no result"))
            continue
        by_hash_to_seq.update(res["txHashToSeq"])
        total_gas += int(res["totalGas"])
        num_updates += 1 if res.get("updateIncluded") else 0
        num_recv += res.get("recvCount", 0)

    for p in committed:
        chain_seq = by_hash_to_seq.get(p.tx_hash.upper())
        if not chain_seq:
            p.status = "failed"
            p.error = "tx hash not relayed (its chunk failed)"
            continue
        p.chain_seq = chain_seq
        chunk_idx = tx_hash_to_chunk_idx.get(p.tx_hash)
        if chunk_idx is not None and chunk_finish_ts[chunk_idx] is not None:
            p.credited_ts = chunk_finish_ts[chunk_idx]
        # ack_pool.py needs one place to look for recv-result files —
        # relay-chunk.js already writes them directly into out_root (shared
        # across concurrent chunks; see run_chunk's comment on why that's safe).
        p.status = "credited"

    log(f"  pooled relay: {len(chunks)} chunk(s) across {W} concurrent account(s) in {elapsed:.1f}s — "
        f"{len(chunks) - len(failed_chunks)} ok, {len(failed_chunks)} failed. "
        f"total gas {total_gas}, {num_updates}/{len(chunks) - len(failed_chunks)} chunks included "
        f"a light-client update (TRUE count, not idealized)")

    aggregated = {
        "groupSize": len(committed),
        "chunkSize": chunk_size,
        "numChunks": len(chunks),
        "numChunkFailures": len(failed_chunks),
        "poolSize": W,
        "relayConcurrency": W,
        "totalGas": str(total_gas),
        "numUpdateClientCalls": num_updates,
        "numRecvPacketCalls": num_recv,
        "elapsedSeconds": elapsed,
        "txHashToSeq": by_hash_to_seq,
    }

    if failed_chunks:
        msg = "; ".join(f"chunk {i}: {e}" for i, e in failed_chunks[:5])
        return "partial_relay_failure", f"{len(failed_chunks)}/{len(chunks)} chunks failed: {msg}", aggregated
    return "", "", aggregated
