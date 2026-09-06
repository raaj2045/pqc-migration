#!/usr/bin/env python3
"""Preconditions for the Ethereum -> Cosmos migration volume experiment.

WHY THIS IS NOT batch_scaling's check_setup.py
----------------------------------------------
batch_scaling measures the Cosmos -> EVM direction, where the measured
(batched) leg is proved by SP1 and verified on the EVM by
SP1ICS07Tendermint. There, binding the client to SP1MockVerifier is a hard
REQUIREMENT: with the real Groth16 verifier, every group-size cell silently
becomes a proving-time measurement instead of a mechanism measurement.

This experiment measures the opposite direction, and the legs swap roles:

    FORWARD (EVM -> Cosmos)   the measured leg. Verified by
                              cw-ics08-wasm-eth: real 512-key sync-committee
                              BLS in MsgUpdateClient, then an MPT membership
                              proof per packet against the execution state
                              root. No SP1, no proof-api, no mock anywhere on
                              this path — it cannot be mocked away, and it is
                              bound by real Ethereum finality.

    ACK (Cosmos -> EVM)       the non-headline leg. This is the one proof-api
                              and SP1 serve.

So the mock-verifier assertion does NOT transfer as-written. Restated for
this direction:

  1. HARD REQUIREMENT: the verifier/prover PAIR must match (mock<->mock or
     real<->real). A mismatched pair reverts every ack, exactly as in
     batch_scaling — SP1MockVerifier's body asserts the proof bytes are
     empty. This half of the check is direction-independent and is kept.

  2. NOT a requirement, but reported loudly: WHICH side of that pair is
     bound. Mock is RECOMMENDED here, for a different reason than in
     batch_scaling: not because a real proof would corrupt the headline
     number (it would not — the headline is `credited`, set on the forward
     leg, which SP1 never touches), but because ~10 min/proof on the ack leg
     would dominate wall-clock time and cap the sweep's scale for no
     measurement benefit. Running against the real verifier is therefore a
     legitimate, if slow, configuration — so this is a warning, not a
     failure. That is the substantive difference from batch_scaling, where
     the same setting is fatal to the result's meaning.

  3. NEW, and required here only: the beacon chain must be reachable AND
     ACTUALLY FINALIZING, and eth_getProof must work at a finalized block.
     batch_scaling needs neither (its measured leg is mock-verified and not
     finality-bound). Here the measured leg cannot make progress at all if
     finality stalls, so a stalled beacon must fail loudly up front rather
     than as a 16-minute timeout inside the first relay.

  4. NEW: config-hazard checks for two live bugs in devnet.env resolution
     that break exactly this direction. See README.md's "Config hazards".

Run standalone:  python3 check_setup.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "devnet" / "lib"))
import config  # noqa: E402

VERIFIER_SELECTOR = "0x08c84e70"   # keccak256("VERIFIER()")[:4]
MOCK_PROVERS = {"mock"}
REAL_PROVERS = {"cpu", "cuda", "network", "env"}


class SetupError(RuntimeError):
    """A precondition failed. The message is meant to be read by a human."""


def _rpc(url, method, params, timeout=20):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.load(r)
    if "error" in out:
        raise SetupError(f"{method} failed: {out['error']}")
    return out["result"]


def _get(url, timeout=20):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _rpc_url(cfg):
    rpc = cfg.get("GETH_RPC")
    if not rpc:
        raise SetupError("GETH_RPC is not set (generated into $DEVNET_DIR/ports.env by write-ports-env.sh)")
    return rpc if rpc.startswith("http") else f"http://{rpc}"


def check_cosmos_reachable(cfg) -> int:
    r = subprocess.run([cfg["PQCHAIND_BIN"], "status", "--node", cfg["CHAIN_NODE"]],
                       capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise SetupError(f"Cosmos chain not reachable at {cfg['CHAIN_NODE']}: "
                         f"{r.stderr[-300:] or r.stdout[-300:]}")
    return int(json.loads(r.stdout)["sync_info"]["latest_block_height"])


def check_cosmos_light_client_active(cfg) -> str:
    """The client that verifies THE MEASURED LEG. Most important check here."""
    client_id = cfg.get("COSMOS_CLIENT_ID")
    if not client_id:
        raise SetupError("COSMOS_CLIENT_ID is not set — run devnet/scripts/create-light-client.sh")
    r = subprocess.run([cfg["PQCHAIND_BIN"], "query", "ibc", "client", "status", client_id,
                        "--node", cfg["CHAIN_NODE"], "-o", "json"],
                       capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise SetupError(f"light client {client_id} query failed: {r.stderr[-300:] or r.stdout[-300:]}")
    status = (r.stdout or "").strip().strip('"')
    if "Active" not in status:
        raise SetupError(
            f"Cosmos-side light client {client_id} is {status!r}, not Active. This client "
            f"verifies the leg this experiment measures — nothing can be relayed until it is "
            f"Active. Try: python3 devnet/relayer/update-eth-client.py {client_id}")
    return client_id


def check_beacon_finalizing(cfg, settle_s: float = 0.0) -> dict:
    """Beacon reachable AND finality actually advancing.

    Required for this direction only: the measured leg proves against a
    FINALIZED execution block, so a stalled beacon means the experiment can
    never make progress. Failing here costs seconds; failing inside the first
    relay costs a 16-minute poll timeout.
    """
    beacon = (cfg.get("BEACON_URL") or "").rstrip("/")
    if not beacon:
        raise SetupError("BEACON_URL is not set (from $DEVNET_DIR/ports.env's BEACON)")
    try:
        fin = _get(f"{beacon}/eth/v1/beacon/light_client/finality_update")["data"]
    except Exception as e:
        raise SetupError(f"cannot reach beacon at {beacon}: {e}") from e
    slot = int(fin["finalized_header"]["beacon"]["slot"])
    block = int(fin["finalized_header"]["execution"]["block_number"])
    head = int(_rpc(_rpc_url(cfg), "eth_blockNumber", []), 16)
    if settle_s:
        time.sleep(settle_s)
        fin2 = _get(f"{beacon}/eth/v1/beacon/light_client/finality_update")["data"]
        if int(fin2["finalized_header"]["beacon"]["slot"]) <= slot:
            raise SetupError(
                f"beacon finality did not advance past slot {slot} in {settle_s}s — finality "
                f"appears stalled. The measured (EVM -> Cosmos) leg proves against finalized "
                f"blocks and cannot progress.")
    return {"finalized_slot": slot, "finalized_block": block, "head_block": head,
            "lag_blocks": head - block}


def check_eth_getproof(cfg) -> int:
    """eth_getProof must work at a finalized block — it is the proof source
    for the measured leg. A node running without archive/proof support fails
    here rather than mid-run."""
    url = _rpc_url(cfg)
    router = cfg.get("ICS26_ROUTER")
    if not router:
        raise SetupError("ICS26_ROUTER is not set (from $DEVNET_DIR/deploy.env)")
    code = _rpc(url, "eth_getCode", [router, "latest"])
    if code == "0x":
        raise SetupError(f"no contract code at ICS26_ROUTER {router}")
    beacon = (cfg.get("BEACON_URL") or "").rstrip("/")
    fin = _get(f"{beacon}/eth/v1/beacon/light_client/finality_update")["data"]
    block = int(fin["finalized_header"]["execution"]["block_number"])
    slot_key = "0x" + "0" * 64
    proof = _rpc(url, "eth_getProof", [router, [slot_key], hex(block)])
    if "accountProof" not in proof:
        raise SetupError(f"eth_getProof at finalized block {block} returned no accountProof")
    return block


def check_erc20(cfg) -> str:
    token = cfg.get("TEST_ERC20")
    if not token:
        raise SetupError("TEST_ERC20 is not set — run devnet/scripts/deploy-test-token.sh")
    if _rpc(_rpc_url(cfg), "eth_getCode", [token, "latest"]) == "0x":
        raise SetupError(f"no contract code at TEST_ERC20 {token} — it was deployed against a "
                         f"previous enclave; re-run devnet/scripts/deploy-test-token.sh")
    return token


def bound_verifier_kind(cfg) -> tuple[str, str]:
    sp1 = cfg.get("SP1_ICS07")
    if not sp1:
        raise SetupError("SP1_ICS07 is not set — run devnet/create-eth-client.js")
    bound = _rpc(_rpc_url(cfg), "eth_call", [{"to": sp1, "data": VERIFIER_SELECTOR}, "latest"])
    addr = "0x" + bound[-40:]
    mock = (cfg.get("SP1_VERIFIER_MOCK") or "").lower()
    groth = (cfg.get("SP1_VERIFIER_GROTH16") or "").lower()
    if mock and addr.lower() == mock:
        return "mock", addr
    if groth and addr.lower() == groth:
        return "real", addr
    raise SetupError(f"EVM light client at {sp1} is bound to {addr}, matching neither "
                     f"SP1_VERIFIER_MOCK nor SP1_VERIFIER_GROTH16")


def running_proof_api_prover(cfg) -> tuple[str, str]:
    """Prover of the RUNNING proof-api, from its own /proc command line."""
    cfg_path = None
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            args = [a.decode("utf-8", "replace") for a in (proc / "cmdline").read_bytes().split(b"\0") if a]
        except (OSError, PermissionError):
            continue
        if not args or "proof-api" not in args[0]:
            continue
        for i, a in enumerate(args):
            if a == "--config" and i + 1 < len(args):
                cfg_path = Path(args[i + 1])
            elif a.startswith("--config="):
                cfg_path = Path(a.split("=", 1)[1])
        break
    if cfg_path is None:
        raise SetupError("no running proof-api process found — the ack leg cannot be relayed. "
                         "Start it against this devnet (see devnet/README.md).")
    if not cfg_path.exists():
        raise SetupError(f"running proof-api's config {cfg_path} does not exist")
    doc = json.loads(cfg_path.read_text())
    for mod in doc.get("modules", []):
        mode = mod.get("config", {}).get("mode", {})
        sp1 = mode.get("sp1") or {}
        prover = (sp1.get("sp1_prover") or {}).get("type")
        if prover:
            return prover, str(cfg_path)
    raise SetupError(f"could not find an sp1_prover type in {cfg_path}")


def check_verifier_prover_pair(cfg, log) -> dict:
    """HARD: the pair must match. SOFT: which side it is."""
    kind, addr = bound_verifier_kind(cfg)
    prover, cfg_path = running_proof_api_prover(cfg)
    prover_kind = "mock" if prover in MOCK_PROVERS else "real" if prover in REAL_PROVERS else "unknown"
    if prover_kind == "unknown":
        raise SetupError(f"proof-api's sp1_prover type {prover!r} (in {cfg_path}) is neither a "
                         f"known mock nor a known real prover")
    if prover_kind != kind:
        raise SetupError(
            f"MISMATCHED PAIR: the EVM light client is bound to the {kind} verifier ({addr}) but "
            f"the running proof-api uses the {prover_kind} prover ({prover!r}, from {cfg_path}). "
            f"Every acknowledgement relay will revert. Rebind with "
            f"'node devnet/create-eth-client.js --verifier={prover_kind}' or restart proof-api "
            f"with a {kind} prover.")
    if kind == "real":
        log("  WARNING: verifier/prover pair is REAL (Groth16, ~10 min/proof).")
        log("    This does NOT corrupt the headline `credited` measurement — SP1 never touches")
        log("    the forward (EVM -> Cosmos) leg. It only makes the ack leg dominate wall-clock")
        log("    time. Valid but slow; use --verifier=mock to scale the sweep.")
    return {"verifier": kind, "verifier_address": addr, "prover": prover, "proof_api_config": cfg_path}


def check_config_hazards(cfg, log) -> list[str]:
    """Guard the two devnet.env resolution properties this direction depends on.

    Both were live bugs once and are now fixed at the source (see
    devnet/lib/config.js's SHELL_OWNED and devnet/lib/lib.js's signerAddress).
    They are re-checked here because a reintroduced one fails only after a full
    finality wait, on the Cosmos side, with an opaque message.
    """
    warns = []
    receiver = cfg.get("COSMOS_RECEIVER")
    if receiver and not receiver.startswith("cosmos1"):
        warns.append(
            f"COSMOS_RECEIVER resolves to {receiver!r}, not a bech32 Cosmos address. "
            f"devnet/step-native-send.js uses it as the packet receiver.")
    if cfg.get("USER"):
        warns.append(
            "devnet.env still carries a USER entry. It collides with the login "
            "name every shell exports; config's SHELL_OWNED ignores the ambient "
            "value, but the entry itself should be COSMOS_RECEIVER.")
    if cfg.get("VALIDATOR"):
        warns.append(
            "devnet.env still carries a VALIDATOR entry. A message's `signer` "
            "must be the address of the key that actually signs the tx, so it is "
            "resolved live from RELAYER_KEY via the keyring; a pinned address "
            "drifts and the ante handler rejects the tx.")
    for w in warns:
        log(f"  WARNING: {w}")
    return warns


def run_all(cfg, log=print) -> dict:
    out = {}
    out["cosmos_height"] = check_cosmos_reachable(cfg)
    log(f"  Cosmos chain reachable (height {out['cosmos_height']})")
    out["cosmos_client"] = check_cosmos_light_client_active(cfg)
    log(f"  Cosmos light client {out['cosmos_client']} is Active (verifies the MEASURED leg)")
    out["beacon"] = check_beacon_finalizing(cfg)
    log(f"  beacon finalizing: slot {out['beacon']['finalized_slot']}, exec block "
        f"{out['beacon']['finalized_block']}, head {out['beacon']['head_block']} "
        f"(lag {out['beacon']['lag_blocks']} blocks)")
    out["proof_block"] = check_eth_getproof(cfg)
    log(f"  eth_getProof works at finalized block {out['proof_block']}")
    out["test_erc20"] = check_erc20(cfg)
    log(f"  TestERC20 live at {out['test_erc20']}")
    out["pair"] = check_verifier_prover_pair(cfg, log)
    log(f"  verifier/prover pair OK: {out['pair']['verifier']} verifier "
        f"<-> {out['pair']['prover']} prover (ack leg only)")
    out["warnings"] = check_config_hazards(cfg, log)
    return out


def main():
    cfg = config.require(config.load(), "PQCHAIND_BIN", "CHAIN_HOME", "CHAIN_ID", "CHAIN_NODE",
                         "BEACON_URL", "GETH_RPC", "COSMOS_CLIENT_ID", "ETH_CLIENT_ID",
                         "ICS26_ROUTER", "ICS20_TRANSFER", "SP1_ICS07", "SENDTX_CMD")
    try:
        run_all(cfg)
    except SetupError as e:
        print(f"SETUP CHECK FAILED: {e}")
        sys.exit(1)
    print("all preconditions OK")


if __name__ == "__main__":
    main()
