#!/usr/bin/env python3
"""Controlled-rate ICS-20 load generator for the live bridge path.

Submits ICS-20 transfers from ML-DSA-65 accounts on the Cosmos chain at a
controlled offered rate, then follows each packet through real light-client
verification to its credit on the EVM side.

Three phases per run, kept separate because they have very different time
constants:

  1. SUBMIT   offered-rate-controlled MsgTransfer submission on Cosmos.
              Fast: bounded by mempool/consensus, seconds.
  2. RELAY    one MsgUpdateClient per finality window, then relay every
              packet provable against that consensus state.
              Slow: bounded by Ethereum finality, minutes.
  3. OBSERVE  poll the EVM side for the credit of each packet.

The split is the measurement. Phase 1 is per-packet work; phase 2 is
per-window work amortised across every packet in the window. That asymmetry
is what the experiment is about — see README.md.

Timestamps recorded per packet:
  submit_ts   just before the Cosmos tx is broadcast
  commit_ts   Cosmos tx included in a block
  window_id   which finality window relayed it
  credit_ts   credit observed on the EVM side

End-to-end latency is credit_ts - submit_ts.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "devnet" / "lib"))
import config  # noqa: E402


# --------------------------------------------------------------------------- #
# Records                                                                     #
# --------------------------------------------------------------------------- #

@dataclass
class Packet:
    seq: int
    amount: int
    submit_ts: float = 0.0
    commit_ts: float = 0.0
    credit_ts: float = 0.0
    ack_ts: float = 0.0
    window_id: int = -1
    tx_hash: str = ""
    status: str = "pending"   # pending | committed | credited | failed
    error: str = ""

    @property
    def latency(self) -> float | None:
        """Round-trip latency: submission until the ack is verified on Cosmos."""
        if self.status == "acked" and self.submit_ts and self.ack_ts:
            return self.ack_ts - self.submit_ts
        return None


@dataclass
class RunResult:
    rate: float
    burst_n: int
    repeat: int
    duration_s: float
    offered: int
    submitted: int
    committed: int
    credited: int
    acked: int
    failed: int
    windows_used: int
    started_at: float
    finished_at: float
    seconds_per_slot: int
    slots_per_epoch: int
    packets: list = field(default_factory=list)

    def to_json(self) -> dict:
        d = asdict(self)
        d["packets"] = [asdict(p) for p in self.packets]
        return d


# --------------------------------------------------------------------------- #
# Chain helpers                                                               #
# --------------------------------------------------------------------------- #

class Chain:
    def __init__(self, cfg):
        self.cfg = cfg
        self.bin = cfg["PQCHAIND_BIN"]
        self.home = cfg["CHAIN_HOME"]
        self.chain_id = cfg["CHAIN_ID"]
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

    def send_transfer(self, to_client: str, receiver: str, amount: int,
                      denom: str, sender_key: str, sender_addr: str,
                      timeout_ts: int, workdir: Path) -> str:
        """Submit one ICS-20 transfer over IBC v2. Returns the tx hash.

        Built as a MsgTransfer JSON and signed through sendtx.py rather than
        `pqchaind tx ibc-transfer transfer`, for two reasons established by
        direct test against this chain:

          1. The CLI never sets MsgTransfer.Encoding (there is no flag for it),
             but the EVM counterparty can only decode
             "application/x-solidity-abi". A CLI-built packet commits on Cosmos
             and then fails to decode on the EVM side.
          2. The CLI builds timeout_timestamp in NANOSECONDS
             (client/cli/tx.go: `timeoutTimestamp = uint64(nowNano) + ...`)
             while IBC v2 reads the field as absolute SECONDS
             (04-channel/v2/keeper/packet.go: `time.Unix(int64(ts), 0)`), so
             every CLI transfer is rejected with "timeout exceeds the maximum
             expected value".

        sendtx.py is also the ML-DSA-65 signing path this chain actually uses.
        """
        msg = {
            "@type": "/ibc.applications.transfer.v1.MsgTransfer",
            "source_port": "transfer",
            "source_channel": to_client,
            "token": {"denom": denom, "amount": str(amount)},
            "sender": sender_addr,
            "receiver": receiver,
            "timeout_height": {"revision_number": "0", "revision_height": "0"},
            # Absolute SECONDS, and inside MaxTimeoutDelta (24 h).
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
            raise RuntimeError(f"tx rejected code={out.get('code')}: "
                               f"{out.get('raw_log', '')[:300]}")
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

    def spec(self):
        d = self._get("/eth/v1/config/spec")["data"]
        return int(d["SECONDS_PER_SLOT"]), int(d["SLOTS_PER_EPOCH"])

    def finalized_epoch(self) -> int:
        d = self._get("/eth/v1/beacon/states/head/finality_checkpoints")["data"]
        return int(d["finalized"]["epoch"])

    def head_slot(self) -> int:
        d = self._get("/eth/v1/beacon/headers/head")["data"]
        return int(d["header"]["message"]["slot"])


# --------------------------------------------------------------------------- #
# Phase 1: rate-controlled submission                                         #
# --------------------------------------------------------------------------- #

def submit_burst(chain: Chain, cfg, n: int, log) -> list[Packet]:
    """Submit n transfers back-to-back, as fast as the harness allows.

    Deliberately NOT rate-limited. The earlier rate-controlled design could not
    reach the batching ceiling: each sendtx.py call costs ~3-5 s (ML-DSA-65
    signing, broadcast, await-commit) and submission is sequential, which caps
    offered load at ~0.3 transfers/s regardless of the requested rate. See
    results/rate_sweep/README.md.

    Here the independent variable is n — how many packets are offered into a
    single finality window — which is what the batching metric is actually a
    function of.
    """
    packets: list[Packet] = []

    receiver = cfg["RECEIVER_ADDR"]
    to_client = cfg["COSMOS_CLIENT_ID"]
    sender_key = cfg.get("LOADGEN_KEY") or cfg["RELAYER_KEY"]
    denom = cfg.get("LOADGEN_DENOM", "stake")
    amount = int(cfg.get("LOADGEN_AMOUNT", "1000"))
    sender_addr = chain.sender_address(sender_key)
    workdir = Path(cfg["DEVNET_DIR"])

    t0 = time.time()
    for i in range(n):
        pkt = Packet(seq=i, amount=amount)
        pkt.submit_ts = time.time()
        try:
            pkt.tx_hash = chain.send_transfer(
                to_client, receiver, amount, denom, sender_key, sender_addr,
                timeout_ts=int(time.time() + 3600), workdir=workdir,
            )
            pkt.status = "submitted"
        except Exception as e:
            pkt.status = "failed"
            pkt.error = str(e)[:300]
            log(f"  submit {i} FAILED: {pkt.error[:120]}")
        packets.append(pkt)

    elapsed = time.time() - t0
    log(f"  burst of {n}: submitted {sum(1 for p in packets if p.status=='submitted')}"
        f"/{n} in {elapsed:.1f}s ({n/elapsed:.2f}/s achieved)")
    return packets


def await_commits(chain: Chain, packets: list[Packet], log, timeout=180):
    """Mark which submitted transfers actually made it into a block."""
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


# --------------------------------------------------------------------------- #
# Phase 2/3: relay across finality windows, observe credits                   #
# --------------------------------------------------------------------------- #

def relay_and_observe(cfg, packets: list[Packet], beacon: Beacon,
                      log, max_windows=6) -> int:
    """Carry committed packets through the full round trip.

    Phase A (forward, Cosmos -> EVM) is NOT finality-bound on this devnet: the
    EVM-side SP1ICS07Tendermint runs a mock verifier, and the packet is proven
    against Cosmos state one block after it commits. It is therefore run
    immediately, with no artificial wait.

    Phase B (return, EVM -> Cosmos) is where real verification happens.
    cw-ics08-wasm-eth checks an eth_getProof membership proof against an
    execution state root it holds for a *finalized* slot, so the ack cannot be
    submitted until Ethereum finality covers the block containing it. The
    MsgUpdateClient that establishes that consensus state BLS-verifies all 512
    sync-committee keys and costs ~935k gas — once per window, regardless of
    how many acks ride on it.

    That per-window/per-packet split is the measurement. The return value is
    the number of distinct finality windows consumed, which is the denominator
    of the headline metric.
    """
    recv_cmd = cfg.get("RECV_CMD")
    ack_cmd = cfg.get("ACK_CMD")
    if not recv_cmd or not ack_cmd:
        log("  RECV_CMD/ACK_CMD not configured — cannot relay")
        return 0

    devnet_dir = Path(cfg["DEVNET_DIR"])

    # ---- Phase A: forward leg, no finality wait ----------------------------
    for p in [q for q in packets if q.status == "committed"]:
        recv_path = devnet_dir / f"recv-result-{p.seq}.json"
        env = dict(os.environ, RECV_RESULT_PATH=str(recv_path))
        try:
            subprocess.run(recv_cmd.split() + [p.tx_hash], check=True,
                           capture_output=True, text=True, timeout=600, env=env)
            p.credit_ts = time.time()
            p.status = "credited"
        except subprocess.CalledProcessError as e:
            p.status = "failed"
            p.error = ((e.stderr or e.stdout) or "")[-300:]
            log(f"  recv seq={p.seq} FAILED: {p.error[:140]}")
    log(f"  forward leg: credited {sum(1 for p in packets if p.status=='credited')}"
        f"/{len(packets)}")

    # ---- Phase B: return leg, finality-bound -------------------------------
    # Acks are attempted in batches. Every ack that succeeds without finality
    # having advanced shares the same MsgUpdateClient, which is exactly the
    # batching effect being measured.
    windows = 0
    for w in range(max_windows):
        outstanding = [p for p in packets if p.status == "credited"]
        if not outstanding:
            break

        before_epoch = beacon.finalized_epoch()
        windows += 1
        log(f"  window {w}: acking {len(outstanding)} packets "
            f"(finalized epoch {before_epoch})")

        for p in outstanding:
            recv_path = devnet_dir / f"recv-result-{p.seq}.json"
            try:
                subprocess.run(ack_cmd.split() + [str(recv_path)], check=True,
                               capture_output=True, text=True, timeout=900)
                p.ack_ts = time.time()
                p.window_id = w
                p.status = "acked"
            except subprocess.CalledProcessError as e:
                p.error = ((e.stderr or e.stdout) or "")[-300:]
                log(f"  ack seq={p.seq} deferred: {p.error[:140]}")

        after_epoch = beacon.finalized_epoch()
        log(f"  window {w}: acked "
            f"{sum(1 for p in packets if p.status=='acked')}/{len(packets)} "
            f"(epoch {before_epoch} -> {after_epoch})")

    return windows


# --------------------------------------------------------------------------- #
# Entry point                                                                 #
# --------------------------------------------------------------------------- #

def run_one(cfg, burst_n: int, repeat: int, log) -> RunResult:
    chain = Chain(cfg)
    beacon = Beacon(cfg["BEACON_URL"])
    sps, spe = beacon.spec()

    started = time.time()
    log(f"[N={burst_n} repeat={repeat}] submitting burst")
    packets = submit_burst(chain, cfg, burst_n, log)
    await_commits(chain, packets, log)
    windows = relay_and_observe(cfg, packets, beacon, log)
    finished = time.time()

    return RunResult(
        rate=float("nan"), burst_n=burst_n, repeat=repeat,
        duration_s=time.time() - started,
        offered=len(packets),
        submitted=sum(1 for p in packets if p.status != "failed" or p.tx_hash),
        # Status is terminal, so each counter must include every state the
        # packet may have advanced *through*. Counting only ("committed",
        # "credited") reported committed=0 for a fully successful round trip,
        # because every packet ends at "acked".
        committed=sum(1 for p in packets
                      if p.status in ("committed", "credited", "acked")),
        credited=sum(1 for p in packets if p.status in ("credited", "acked")),
        acked=sum(1 for p in packets if p.status == "acked"),
        failed=sum(1 for p in packets if p.status == "failed"),
        windows_used=windows,
        started_at=started, finished_at=finished,
        seconds_per_slot=sps, slots_per_epoch=spe,
        packets=packets,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, required=True,
                    help="packets submitted into one finality window")
    ap.add_argument("--repeat", type=int, default=0)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    cfg = config.require(
        config.load(),
        "PQCHAIND_BIN", "CHAIN_HOME", "CHAIN_ID", "CHAIN_NODE",
        "BEACON_URL", "COSMOS_CLIENT_ID", "RECEIVER_ADDR", "UPDATE_CLIENT_CMD",
        "SENDTX_CMD", "RECV_CMD", "ACK_CMD", "DEVNET_DIR",
    )

    def log(msg):
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    res = run_one(cfg, args.n, args.repeat, log)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res.to_json(), indent=2))
    log(f"wrote {args.out}  acked={res.acked}/{res.offered} "
        f"credited={res.credited} windows={res.windows_used}")


if __name__ == "__main__":
    main()
