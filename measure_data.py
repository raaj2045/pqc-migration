#!/usr/bin/env python3
"""Sweep driver for the migration_volume experiment (Ethereum -> Cosmos).

For each cell (N users x trial x signer key type) it submits N independent
`sendTransfer` calls on the EVM, relays all N to Cosmos as one batched receive
transaction, and records cost and latency into migration_metrics_detailed.csv.

    python3 measure_data.py [--n=1,10,50,100] [--trials=3]
                            [--signers=validator,relayer] [--out=FILE]
                            [--amount=2000] [--resume]

See experiments/migration_volume/README.md for what each column means.

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
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = HERE / "experiments" / "migration_volume"
sys.path.insert(0, str(HERE / "devnet" / "lib"))
import config  # noqa: E402

DEFAULT_N = [1, 10, 50, 100]
DEFAULT_TRIALS = 3
# validator is secp256k1, relayer is ML-DSA-65 (devnet/scripts/init-chain.sh).
DEFAULT_SIGNERS = ["validator", "relayer"]

# Measured per-packet cost of a receive transaction, from CEILING-FINDINGS.md.
# Used only for the pre-flight capacity check below; the sweep never relies on
# it for a reported number.
BYTES_PER_PACKET = 6_300      # slope, rounded up (6,000-6,212 observed)
BYTES_INTERCEPT = 5_200       # ML-DSA-65 signer, the larger of the two
CEILING_SAFETY = 0.90         # refuse to start above 90% of a measured wall

COLUMNS = [
    "N_Users",
    "Trial",
    "Signer_Key_Type",
    "Signer_Key",
    "Dest_Key_Type",
    "Run_Label",
    "EVM_Gas_Per_User",
    "EVM_Gas_Total",
    "Cosmos_Gas_Total",
    "Cosmos_Gas_Per_Transfer",
    "Recv_Gas_Per_Transfer",
    "Update_Client_Gas",
    "Recv_Packet_Gas",
    "Recv_Tx_Bytes",
    "Throughput_TPS",
    "Credited_Height",
    "Credited_Block_Ts",
    "Chain_Host_Skew_s",
    "T_Submit_s",
    "T_Finality_Wait_s",
    "T_Proof_and_Relay_s",
    "T_Unattributed_s",
    "T_Total_Latency_s",
]


def run(cmd, label):
    """Run a step, streaming its output. Any non-zero exit aborts the sweep."""
    print(f"    $ {' '.join(str(c) for c in cmd)}")
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0:
        raise SystemExit(f"[{label}] failed with exit {r.returncode} — aborting sweep")


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
    the two walls binds depends on node configuration; CEILING-FINDINGS.md
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

    A cell that fails on size mid-sweep wastes every trial before it, so the
    check happens once, up front, against the node's live limits.
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
            f"lower --n; see experiments/migration_volume/CEILING-FINDINGS.md.")
    print(f"  OK: N={max_n} fits with {ceiling - max_n} packet(s) of headroom\n")
    return ceiling


# --- one cell ---------------------------------------------------------------

def load_cell(out_dir, label, n):
    """Read a cell's two artifacts, or return None if it has not run.

    Both are addressed by label, never by mtime, and each is checked to carry
    the label it was asked for — so a file left behind by a different cell is
    rejected rather than silently adopted.
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
    return send, recv


def run_cell(cfg, out_dir, n, trial, signer_key, amount, reuse=False):
    label = f"n{n}-t{trial}-{signer_key}"
    print(f"\n=== N={n} trial={trial} signer={signer_key} (label {label}) ===")

    # A cell costs a full finality wait, so a complete-but-unrecorded one is
    # salvaged rather than re-run. --resume only; a default run always
    # re-measures.
    cached = load_cell(out_dir, label, n) if reuse else None
    if cached:
        print(f"    reusing existing artifacts for {label} (both files complete)")
        send, recv = cached
        send_path = out_dir / f"send-{label}.json"
        recv_path = out_dir / f"recv-{label}.json"
    else:
        run(["node", str(EXP / "setup-user-pool.js"), str(n)], f"{label}/setup")
        run(["node", str(EXP / "submit-migrations.js"), str(n), str(amount),
             f"--label={label}"], f"{label}/submit")

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
             f"--count={n}", f"--label={label}", f"--signer-key={signer_key}"],
            f"{label}/relay")

        recv_path = out_dir / f"recv-{label}.json"
        if not recv_path.exists():
            raise SystemExit(f"{label}: relay-recv-batch.js did not write {recv_path}")
        loaded = load_cell(out_dir, label, n)
        if not loaded:
            raise SystemExit(f"{label}: artifacts incomplete after a successful run")
        send, recv = loaded

    # --- gas ----------------------------------------------------------------
    # MsgUpdateClient and the N MsgRecvPacket are SEPARATE transactions, so the
    # receive tx's own gas_used is the packet-delivery cost by itself. The
    # Cosmos-side total is the sum, not a figure to subtract the update out of.
    evm_total = int(require(send, "submitGasTotal", str(send_path)))
    recv_gas = int(require(recv, "gasUsed", str(recv_path)))
    update_gas = int(require(recv, "updateGas", str(recv_path)))
    cosmos_total = recv_gas + update_gas

    # --- time ---------------------------------------------------------------
    # Every span below is on the RELAY HOST'S clock, and so is the total. The
    # Cosmos block header time is a chain clock: CometBFT sets it from the
    # median of the previous commit's validator timestamps, so it lags the host
    # by seconds. Subtracting one from the other silently books that skew as
    # negative unattributed time, so the two clocks are kept apart and the
    # offset is recorded as its own column instead.
    t_submit = float(require(send, "elapsedSeconds", str(send_path)))
    t_finality = float(require(recv, "finalityWaitSeconds", str(recv_path), positive=False))
    t_relay = sum(float(require(recv, k, str(recv_path), positive=False))
                  for k in ("updateSeconds", "proofSeconds", "txSeconds"))
    t_start = float(require(send, "firstSubmittedTs", str(send_path)))
    t_credit_host = float(require(recv, "creditedWallTs", str(recv_path)))
    t_credit_chain = float(require(recv, "creditedTs", str(recv_path)))
    t_total = t_credit_host - t_start
    if t_total <= 0:
        raise SystemExit(f"{label}: credited ({t_credit_host}) is not after first submit ({t_start})")

    # Four measured phases plus an explicit residual. The residual is NOT
    # relabelled as the finality wait: it is whatever the phases do not span.
    t_unattributed = t_total - (t_submit + t_finality + t_relay)
    # A materially negative residual means the spans overlap or the clocks were
    # mixed again — either way the decomposition is wrong, so say so rather
    # than plot a negative bar.
    if t_unattributed < -1.0:
        raise SystemExit(
            f"{label}: measured phases sum to {t_submit + t_finality + t_relay:.2f}s, "
            f"more than the {t_total:.2f}s total — the spans overlap or two clocks "
            f"were mixed; refusing to record a negative residual")

    return {
        "N_Users": n,
        "Trial": trial,
        "Signer_Key_Type": recv["signerAlgo"],
        "Signer_Key": recv["signerKey"],
        # Not an axis: receivers are 20-byte bech32 addresses in the payload
        # whatever key would control them, and a fresh recipient carries no
        # pubkey on chain until it first signs. See CEILING-FINDINGS.md.
        "Dest_Key_Type": "none",
        "Run_Label": label,
        "EVM_Gas_Per_User": evm_total / n,
        "EVM_Gas_Total": evm_total,
        "Cosmos_Gas_Total": cosmos_total,
        "Cosmos_Gas_Per_Transfer": cosmos_total / n,
        "Recv_Gas_Per_Transfer": recv_gas / n,
        "Update_Client_Gas": update_gas,
        "Recv_Packet_Gas": recv_gas,
        "Recv_Tx_Bytes": int(require(recv, "txBytes", str(recv_path))),
        "Throughput_TPS": n / t_total,
        "Credited_Height": int(require(recv, "creditedHeight", str(recv_path))),
        "Credited_Block_Ts": t_credit_chain,
        "Chain_Host_Skew_s": t_credit_chain - t_credit_host,
        "T_Submit_s": t_submit,
        "T_Finality_Wait_s": t_finality,
        "T_Proof_and_Relay_s": t_relay,
        "T_Unattributed_s": t_unattributed,
        "T_Total_Latency_s": t_total,
    }


# --- sweep ------------------------------------------------------------------

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
    ap.add_argument("--out", default="migration_metrics_detailed.csv")
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

    out_path = HERE / args.out
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
                f"{out_path} has a {len(header or [])}-column header, but this sweep writes "
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
    total = len(n_users) * args.trials * len(signers)
    i = 0
    with open(out_path, "a" if args.resume else "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if write_header:
            w.writeheader()
        # Signer key type is the INNERMOST loop, so the two arms interleave.
        # Running one arm to completion and then the other confounds key type
        # with anything that drifts over a multi-hour run — most concretely the
        # router's storage trie, which deepens as packets accumulate and moves
        # per-packet proof size (CEILING-FINDINGS.md §5). Interleaving spreads
        # that drift across both arms instead of loading it onto the second.
        for n in n_users:
            for trial in range(1, args.trials + 1):
                for signer_key in signers:
                    i += 1
                    label = f"n{n}-t{trial}-{signer_key}"
                    if label in done:
                        print(f"[{i}/{total}] {label} — already recorded, skipping")
                        continue
                    print(f"[{i}/{total}]", end=" ")
                    w.writerow(run_cell(cfg, out_dir, n, trial, signer_key,
                                       args.amount, reuse=args.resume))
                    f.flush()

    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
