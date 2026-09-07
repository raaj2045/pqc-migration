#!/usr/bin/env python3
"""Measurement runner for the migration_volume experiment (Ethereum -> Cosmos).

For each cell (N users x trial x signer key type) it submits N independent
`sendTransfer` calls on the EVM, relays all N to Cosmos as one batched receive
transaction, and records cost and latency into results/latency_by_step.csv.

    python3 experiments/migration_volume/measure_data.py [--n=1,10,50,100] [--trials=3]
                            [--signers=validator,relayer] [--concurrency=1]
                            [--out=FILE] [--amount=2000] [--resume]

Running migrations at the same time
-----------------------------------
`--concurrency=K` runs K migrations together as one wave. Waiting for Ethereum
finality and updating the light client happen ONCE per wave and are shared;
submitting, proving and delivering happen per migration and run concurrently,
so those numbers carry real contention. A wave needs K signing accounts
(`setup-signer-pool.py`) because two in-flight Cosmos transactions from one
account race for the same sequence number.

Shared steps are recorded against the wave, and the plotter counts their
repeats per wave rather than per row — otherwise one finality measurement
copied across K rows would be read as K independent samples.

See README.md for what each column means.

Measured, not inferred
----------------------
`T_Finality_Wait_s` is the real time relay-recv-batch.js spent blocked on
beacon finality, reported by that script. `T_Unattributed_s` is what is left
of the wall clock after the four measured phases; it is named for what it is
(process startup, polling granularity, inter-phase gaps) rather than being
folded into the finality wait, which would overstate a term the paper quotes.

One clock
---------
Every span and the total are on the relay host's clock. The Cosmos block
header time is a different clock — CometBFT derives it from the median of the
previous commit's validator timestamps, so it lags the host by seconds — and
is carried separately as `Credited_Block_Ts`, with the offset in
`Chain_Host_Skew_s`. Mixing the two books the skew as negative unattributed
time.

Deterministic paths
-------------------
Every cell has a run label, passed to both scripts, and both write files named
by it. Nothing selects a file by mtime/ctime, so a leftover file from an
earlier trial can never be read as this trial's result.
"""
import argparse
import csv
import math
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent          # experiments/migration_volume
REPO = HERE.parent.parent
EXP = HERE                                       # the node scripts live beside this one
RESULTS = HERE / "results"
sys.path.insert(0, str(REPO / "devnet" / "lib"))
import config  # noqa: E402

DEFAULT_N = [1, 10, 50, 100]
DEFAULT_TRIALS = 3
# validator is secp256k1, relayer is ML-DSA-65 (devnet/scripts/init-chain.sh).
DEFAULT_SIGNERS = ["validator", "relayer"]

# Measured per-packet cost of a receive transaction, from LIMITS.md.
# Used only for the pre-flight capacity check below; no reported number
# depends on it.
BYTES_PER_PACKET = 6_300      # slope, rounded up (6,000-6,212 observed)
BYTES_INTERCEPT = 5_200       # ML-DSA-65 signer, the larger of the two
CEILING_SAFETY = 0.90         # refuse to start above 90% of a measured wall

COLUMNS = [
    "N_Users",
    "Trial",
    "Signer_Key_Type",
    "Run_Label",
    "Wave",
    "Flow",
    "Concurrency",
    # gas, one column per operation that costs any
    "Submit_Gas_Total",
    "Submit_Gas_Per_Tx",
    "Update_Client_Gas",
    "Deliver_Gas_Total",
    "Deliver_Gas_Per_Tx",
    "Deliver_Tx_Bytes",
    # time, one column per operation, all on the relay host's clock
    "T_Submit_s",
    "T_Wait_Finality_s",
    "T_Update_Client_s",
    "T_Fetch_Proof_s",
    "T_Deliver_s",
    "T_Other_s",
    "T_Total_s",
    "Transfers_Per_Second",
    "Credited_Height",
]


def run(cmd, label):
    """Run a step, streaming its output. Any non-zero exit stops the run."""
    print(f"    $ {' '.join(str(c) for c in cmd)}")
    r = subprocess.run(cmd, cwd=REPO)
    if r.returncode != 0:
        raise SystemExit(f"[{label}] failed with exit {r.returncode} — stopping")


def require(d, key, where, positive=True):
    """Read a numeric field that must be present and must have parsed.

    Silently defaulting a missing field to 0 is how an unparsed gas figure
    becomes a plausible-looking data point, so every read goes through here.
    Gas totals cross the JSON boundary as strings because they are BigInts on
    the JavaScript side, so the check is that the value *is a number*, not that
    it arrived as one. `positive=False` allows zero (a wait that was already
    satisfied), never a negative or an unparseable value.
    """
    if key not in d or d[key] is None:
        raise SystemExit(f"{where}: missing required field {key!r}; got keys {sorted(d)}")
    v = d[key]
    try:
        num = float(v)
    except (TypeError, ValueError):
        raise SystemExit(f"{where}: field {key!r} is {v!r}, which is not a number")
    if num != num:                                   # NaN
        raise SystemExit(f"{where}: field {key!r} is NaN")
    if positive and num <= 0:
        raise SystemExit(f"{where}: field {key!r} is {v!r}, expected a positive number")
    if not positive and num < 0:
        raise SystemExit(f"{where}: field {key!r} is {v!r}, expected a non-negative number")
    return v


# --- pre-flight -------------------------------------------------------------

def node_limits(cfg):
    """The two size walls a receive transaction has to clear, read from the
    node's live config rather than assumed from an earlier measurement.

    `tx broadcast` base64-encodes the transaction into a JSON-RPC body, so the
    RPC's max_body_bytes caps the raw transaction at 3/4 of its value. Which of
    the two walls binds depends on node configuration; LIMITS.md
    measured 124 packets when max_body_bytes was at its 1 MB default.
    """
    toml = Path(cfg["CHAIN_HOME"]) / "config" / "config.toml"
    if not toml.exists():
        raise SystemExit(f"cannot read node config at {toml}")
    max_body = max_tx = None
    for line in toml.read_text().splitlines():
        line = line.split("#")[0].strip()
        if line.startswith("max_body_bytes"):
            max_body = int(line.split("=")[1].strip())
        elif line.startswith("max_tx_bytes"):
            max_tx = int(line.split("=")[1].strip())
    if not max_body or not max_tx:
        raise SystemExit(f"could not read max_body_bytes/max_tx_bytes from {toml}")
    return max_body, max_tx


def preflight_capacity(cfg, max_n):
    """Confirm the largest cell fits before spending hours reaching it.

    A run that fails on size partway through wastes every repeat before it, so
    the check happens once, up front, against the node's live limits.
    """
    max_body, max_tx = node_limits(cfg)
    rpc_raw_cap = max_body * 3 // 4          # base64 inflates 4/3
    binding, wall = ("RPC max_body_bytes", rpc_raw_cap) if rpc_raw_cap < max_tx \
        else ("mempool max_tx_bytes", max_tx)
    ceiling = (wall - BYTES_INTERCEPT) // BYTES_PER_PACKET
    projected = BYTES_INTERCEPT + BYTES_PER_PACKET * max_n

    print("pre-flight: receive-transaction capacity")
    print(f"  RPC max_body_bytes  {max_body:>10,}  -> {rpc_raw_cap:>9,} B raw")
    print(f"  mempool max_tx_bytes{max_tx:>10,}")
    print(f"  binding wall: {binding} at {wall:,} B -> ~{ceiling} packets/tx")
    print(f"  largest cell N={max_n}: ~{projected:,} B "
          f"({projected / wall * 100:.1f}% of the wall)")
    if projected > wall * CEILING_SAFETY:
        raise SystemExit(
            f"N={max_n} projects to {projected:,} B, over {CEILING_SAFETY:.0%} of the "
            f"{binding} wall ({wall:,} B, ~{ceiling} packets). Chunk the receive or "
            f"lower --n; see experiments/migration_volume/LIMITS.md.")
    print(f"  OK: N={max_n} fits with {ceiling - max_n} packet(s) of headroom\n")
    return ceiling


# --- one cell ---------------------------------------------------------------

def load_cell(out_dir, label, n, cfg=None):
    """Read a cell's two artifacts, or return None if it has not run.

    Both are addressed by label, never by mtime, and each is checked to carry
    the label it was asked for — so a file left behind by a different cell is
    rejected rather than silently adopted.

    When `cfg` is given the artifacts must also name the client IDs currently
    configured. A Kurtosis Ethereum enclave does not survive a host restart, so
    a rebuild redeploys the contracts and issues new client IDs — and labels
    alone would happily match a cell measured against the PREVIOUS devnet,
    whose router storage trie was a different depth. That is a silent
    cross-devnet splice, so it is refused here.
    """
    send_path = out_dir / f"send-{label}.json"
    recv_path = out_dir / f"recv-{label}.json"
    if not (send_path.exists() and recv_path.exists()):
        return None
    send = json.loads(send_path.read_text())
    recv = json.loads(recv_path.read_text())
    for path, doc in ((send_path, send), (recv_path, recv)):
        if doc.get("label") != label:
            raise SystemExit(f"{path}: label is {doc.get('label')!r}, expected {label!r}")
    if not recv.get("ok"):
        raise SystemExit(f"{label}: receive transaction failed — {recv.get('rawLog')}")
    if recv.get("count") != n:
        raise SystemExit(f"{label}: recv summary covers {recv.get('count')} packets, expected {n}")
    # The two files must describe the same packets, not merely share a label.
    sent = {str(p.get("sequence")) for p in send.get("packets", [])
            if p.get("status") == "committed"}
    got = {str(x) for x in recv.get("sequences", [])}
    if not got or not got <= sent:
        raise SystemExit(f"{label}: recv sequences {sorted(got)[:5]}... are not a subset "
                         f"of the send file's committed sequences — mismatched artifacts")
    if cfg:
        for key, field in (("ETH_CLIENT_ID", "sourceClient"), ("COSMOS_CLIENT_ID", "destClient")):
            want, have = cfg.get(key), send.get(field)
            if want and have and want != have:
                raise SystemExit(
                    f"{label}: artifact names {field}={have!r} but this devnet has "
                    f"{key}={want!r} — it was measured against a different devnet. "
                    f"Move {out_dir} aside and re-measure; splicing runs across a "
                    f"contract redeploy changes per-packet proof size.")
    return send, recv


def run_cell(cfg, out_dir, n, trial, signer_key, amount, reuse=False,
             wave=0, flow=0, concurrency=1, pool_offset=0, phase="all",
             shared=None):
    label = f"n{n}-t{trial}-{signer_key}-w{wave}f{flow}" if concurrency > 1 \
        else f"n{n}-t{trial}-{signer_key}"
    print(f"\n=== N={n} trial={trial} signer={signer_key} (label {label}) ===")

    # A cell costs a full finality wait, so a complete-but-unrecorded one is
    # salvaged rather than re-run. --resume only; a default run always
    # re-measures.
    cached = load_cell(out_dir, label, n, cfg) if reuse else None
    if cached:
        print(f"    reusing existing artifacts for {label} (both files complete)")
        send, recv = cached
        send_path = out_dir / f"send-{label}.json"
        recv_path = out_dir / f"recv-{label}.json"
    else:
        run(["node", str(EXP / "submit-migrations.js"), str(n), str(amount),
             f"--label={label}", f"--pool-offset={pool_offset}"], f"{label}/submit")

        # Deterministic paths, both named by the run label. Nothing here globs.
        send_path = out_dir / f"send-{label}.json"
        if not send_path.exists():
            raise SystemExit(f"{label}: submit-migrations.js did not write {send_path}")
        send = json.loads(send_path.read_text())
        if send.get("label") != label:
            raise SystemExit(f"{send_path}: label is {send.get('label')!r}, expected {label!r}")
        committed = require(send, "committed", str(send_path))
        if committed != n:
            raise SystemExit(f"{label}: only {committed}/{n} sendTransfer calls committed")

        run(["node", str(EXP / "relay-recv-batch.js"), str(send_path),
             f"--count={n}", f"--label={label}", f"--signer-key={signer_key}",
             f"--phase={phase}"], f"{label}/relay")

        recv_path = out_dir / f"recv-{label}.json"
        if not recv_path.exists():
            raise SystemExit(f"{label}: relay-recv-batch.js did not write {recv_path}")
        loaded = load_cell(out_dir, label, n, cfg)
        if not loaded:
            raise SystemExit(f"{label}: artifacts incomplete after a successful run")
        send, recv = loaded

    # --- gas ----------------------------------------------------------------
    # Updating the light client and delivering the packets are SEPARATE Cosmos
    # transactions, so each carries its own gas. Nothing is subtracted from
    # anything.
    submit_gas = int(require(send, "submitGasTotal", str(send_path)))
    deliver_gas = int(require(recv, "gasUsed", str(recv_path)))
    update_gas = int(shared["updateGas"]) if shared \
        else int(require(recv, "updateGas", str(recv_path)))

    # --- time ---------------------------------------------------------------
    # One column per operation. Every span and the total come from the relay
    # host's clock; the Cosmos block header runs on a different clock (it lags
    # by seconds), so it is never one end of a subtraction here.
    t_submit = float(require(send, "elapsedSeconds", str(send_path)))
    if shared:
        # Waiting for finality and updating the client happened once for the
        # whole wave. Charging each flow the full wait would inflate the total
        # and, worse, turn one measurement into `concurrency` identical rows
        # that a confidence interval would treat as independent samples.
        t_wait = float(shared["finalityWaitSeconds"])
        t_update = float(shared["updateSeconds"])
    else:
        t_wait = float(require(recv, "finalityWaitSeconds", str(recv_path), positive=False))
        t_update = float(require(recv, "updateSeconds", str(recv_path), positive=False))
    t_proof = float(require(recv, "proofSeconds", str(recv_path), positive=False))
    t_deliver = float(require(recv, "txSeconds", str(recv_path), positive=False))

    t_start = float(require(send, "firstSubmittedTs", str(send_path)))
    t_end = float(require(recv, "creditedWallTs", str(recv_path)))
    t_total = t_end - t_start
    if t_total <= 0:
        raise SystemExit(f"{label}: credited ({t_end}) is not after first submit ({t_start})")

    # Whatever the measured operations do not cover: process start-up, how
    # often the finality check polls, the gaps between steps. Named for what it
    # is rather than folded into an operation.
    t_other = t_total - (t_submit + t_wait + t_update + t_proof + t_deliver)
    if t_other < -1.0:
        raise SystemExit(
            f"{label}: the measured operations sum to "
            f"{t_submit + t_wait + t_update + t_proof + t_deliver:.2f}s, more than the "
            f"{t_total:.2f}s total — they overlap or two clocks were mixed")

    return {
        "N_Users": n,
        "Trial": trial,
        "Signer_Key_Type": recv["signerAlgo"],
        "Run_Label": label,
        "Wave": wave,
        "Flow": flow,
        "Concurrency": concurrency,
        "Submit_Gas_Total": submit_gas,
        "Submit_Gas_Per_Tx": submit_gas / n,
        "Update_Client_Gas": update_gas,
        "Deliver_Gas_Total": deliver_gas,
        "Deliver_Gas_Per_Tx": deliver_gas / n,
        "Deliver_Tx_Bytes": int(require(recv, "txBytes", str(recv_path))),
        "T_Submit_s": t_submit,
        "T_Wait_Finality_s": t_wait,
        "T_Update_Client_s": t_update,
        "T_Fetch_Proof_s": t_proof,
        "T_Deliver_s": t_deliver,
        "T_Other_s": t_other,
        "T_Total_s": t_total,
        "Transfers_Per_Second": n / t_total,
        "Credited_Height": int(require(recv, "creditedHeight", str(recv_path))),
    }


def signer_pool_key(cfg, key_type, flow, base_key):
    """Account this flow signs with. One flow per account: two in-flight Cosmos
    transactions from one account race for the same sequence number."""
    if flow == 0:
        return base_key
    return f"mv-{key_type}-{flow}"


def run_wave(cfg, out_dir, n, trials, signer_key, amount, wave, concurrency,
             reuse, done, pool_size):
    """One wave: `concurrency` migrations of `n` transfers, running together.

    Waiting for Ethereum finality and updating the light client are done ONCE
    and shared. That is both what a real relayer does and what keeps the
    statistics honest: charging every flow for a wait that happened once would
    inflate each flow's total and turn a single measurement into `concurrency`
    identical rows, which a confidence interval would read as independent
    samples and report far too tight an interval.

    Everything that genuinely happens per flow — submitting on Ethereum,
    building proofs, delivering on Cosmos — runs concurrently, so those numbers
    include the contention a real network would have.
    """
    labels = [f"n{n}-t{t}-{signer_key}-w{wave}f{i}" if concurrency > 1
              else f"n{n}-t{t}-{signer_key}"
              for i, t in enumerate(trials)]
    if all(lb in done for lb in labels):
        print(f"wave {wave}: all {len(labels)} flow(s) already recorded, skipping")
        return []

    if concurrency == 1:
        run(["node", str(EXP / "setup-user-pool.js"), str(pool_size or n)],
            f"wave{wave}/setup")
        return [run_cell(cfg, out_dir, n, trials[0], signer_key, amount,
                         reuse=reuse, wave=wave, flow=0, concurrency=1)]

    key_type = "secp256k1" if signer_key == "validator" else "mldsa65"
    print(f"\n=== wave {wave}: {concurrency} x {n} transfer(s), signer {signer_key} ===")

    # Enough EVM accounts for every flow to have its own slice.
    need = pool_size or concurrency * n
    run(["node", str(EXP / "setup-user-pool.js"), str(need)], f"wave{wave}/setup")

    # 1. every flow submits on Ethereum at the same time
    def submit(i):
        label, t = labels[i], trials[i]
        run(["node", str(EXP / "submit-migrations.js"), str(n), str(amount),
             f"--label={label}", f"--pool-offset={i * n}"], f"{label}/submit")
        return json.loads((out_dir / f"send-{label}.json").read_text())

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        sends = list(ex.map(submit, range(len(labels))))

    # 2. one finality wait and one client update, for the whole wave. Use the
    #    flow whose newest send block is highest: finality covering that covers
    #    every other flow too.
    lead = max(range(len(sends)),
               key=lambda i: max(p["blockNumber"] for p in sends[i]["packets"]
                                 if p.get("status") == "committed"))
    run(["node", str(EXP / "relay-recv-batch.js"), str(out_dir / f"send-{labels[lead]}.json"),
         f"--count={n}", f"--label={labels[lead]}", f"--signer-key={signer_key}",
         "--phase=prepare"], f"wave{wave}/prepare")
    shared = json.loads((out_dir / f"prepare-{labels[lead]}.json").read_text())
    print(f"  shared: finality {shared['finalityWaitSeconds']:.1f}s, "
          f"update {shared['updateGas']} gas at slot {shared['useSlot']}")

    # 3. every flow delivers at the same time, each on its own signing account
    def deliver(i):
        label = labels[i]
        key = signer_pool_key(cfg, key_type, i, signer_key)
        # Pin every flow to the state prepare selected, so their proofs are
        # against identical chain state and their costs are comparable.
        run(["node", str(EXP / "relay-recv-batch.js"), str(out_dir / f"send-{label}.json"),
             f"--count={n}", f"--label={label}", f"--signer-key={key}",
             "--phase=deliver", f"--use-slot={shared['useSlot']}"], f"{label}/deliver")

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        list(ex.map(deliver, range(len(labels))))

    return [run_cell(cfg, out_dir, n, trials[i], signer_key, amount, reuse=True,
                     wave=wave, flow=i, concurrency=concurrency, pool_offset=i * n,
                     shared=shared)
            for i in range(len(labels))]


# --- run ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", default=",".join(str(x) for x in DEFAULT_N),
                    help="comma-separated cohort sizes")
    ap.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    ap.add_argument("--signers", default=",".join(DEFAULT_SIGNERS),
                    help="comma-separated keyring key names to sign the receive "
                         "tx with; the key-type axis")
    ap.add_argument("--amount", default="2000", help="tokens migrated per user")
    ap.add_argument("--out", default="latency_by_step.csv",
                    help="written under results/ unless given an absolute path")
    ap.add_argument("--concurrency", type=int, default=1,
                    help="how many migrations run at the same time. Above 1 the "
                         "repeats of a batch size run together, sharing one "
                         "finality wait and one client update, and each flow "
                         "signs with its own account from the pool built by "
                         "setup-signer-pool.py")
    ap.add_argument("--pool-size", type=int, default=None,
                    help="EVM accounts to fund (default: concurrency x max batch)")
    ap.add_argument("--resume", action="store_true",
                    help="append to --out, skipping cells already in it and "
                         "reusing on-disk artifacts for cells that completed "
                         "but were never recorded")
    args = ap.parse_args()

    n_users = [int(x) for x in args.n.split(",") if x]
    signers = [s for s in args.signers.split(",") if s]
    cfg = config.load()
    out_dir = Path(cfg["DEVNET_DIR"]) / "migration-volume"
    out_dir.mkdir(parents=True, exist_ok=True)

    preflight_capacity(cfg, max(n_users))

    RESULTS.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if Path(args.out).is_absolute() else RESULTS / args.out
    done = set()
    has_header = False
    if args.resume and out_path.exists() and out_path.stat().st_size:
        has_header = True
        with open(out_path, newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
        # Appending rows of one shape under a header of another silently
        # produces a file where every column is misread. A resumed run must
        # find exactly the schema it is about to write.
        if header != COLUMNS:
            extra = [c for c in header or [] if c not in COLUMNS]
            missing = [c for c in COLUMNS if c not in (header or [])]
            raise SystemExit(
                f"{out_path} has a {len(header or [])}-column header, but this run writes "
                f"{len(COLUMNS)} columns"
                + (f"\n  missing: {missing}" if missing else "")
                + (f"\n  unexpected: {extra}" if extra else "")
                + f"\nMove it aside and re-run with --resume (completed cells are "
                  f"reconstructed from the artifacts in {cfg['DEVNET_DIR']}/migration-volume), "
                  f"or drop --resume to start a fresh file.")
        with open(out_path, newline="") as f:
            done = {r["Run_Label"] for r in csv.DictReader(f) if r.get("Run_Label")}
        print(f"resuming: {len(done)} cell(s) already in {out_path.name}\n")

    write_header = not has_header
    per_wave = args.concurrency
    waves = math.ceil(args.trials / per_wave)
    total = len(n_users) * args.trials * len(signers)
    i = 0
    with open(out_path, "a" if args.resume else "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if write_header:
            w.writeheader()
        # Signing key type is the INNERMOST loop, so the two arms interleave.
        # Running one arm to completion and then the other confounds key type
        # with anything that drifts over a long run — most concretely the
        # router's storage trie, which deepens as packets accumulate and moves
        # per-packet proof size (LIMITS.md).
        for n in n_users:
            for signer_key in signers:
                for wave in range(waves):
                    trials = [t for t in range(1 + wave * per_wave,
                                               1 + min((wave + 1) * per_wave, args.trials))]
                    if not trials:
                        continue
                    i += len(trials)
                    print(f"[{i}/{total}]", end=" ")
                    rows = run_wave(cfg, out_dir, n, trials, signer_key, args.amount,
                                    wave, len(trials), args.resume, done, args.pool_size)
                    for row in rows:
                        w.writerow(row)
                    f.flush()

    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
