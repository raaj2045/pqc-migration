#!/usr/bin/env python3
"""Preconditions for the batch-scaling sweep.

Confirms, before any transfer is submitted:
  1. The Cosmos chain is reachable.
  2. The Cosmos-side light client (cw-ics08-wasm-eth, tracking Ethereum) is
     Active.
  3. The Ethereum devnet's JSON-RPC is reachable.
  4. The EVM-side light client (SP1ICS07Tendermint, tracking Cosmos) has live
     code and is bound to SP1MockVerifier — NOT SP1VerifierGroth16.
  5. The running proof-api's PROVER matches that verifier.

Check 5 exists because checks 1-4 all passed on a devnet that could not relay
a single packet. The verifier is only half of a pair: SP1MockVerifier's entire
body is `assert(proofBytes.length == 0)`, so a mock-bound client handed a real
Groth16 proof reverts with Panic(0x01) on every relay, and a Groth16-bound
client handed an empty mock proof fails just as surely. Checking the on-chain
side alone is blind to that — a bring-up that bound the mock verifier while
configuring proof-api with a real CPU prover passed every check and then burned
25 minutes per proof to produce a transaction that could never succeed.

That last check is the one this experiment depends on for its whole premise:
grouping transfers is being measured as a property of the transfer mechanism,
not of proving time (~10 min/proof already measured separately, see
../migration_throughput/README.md and ../../devnet/README.md#proving). Running
this sweep against the real verifier would silently turn every group-size cell
into a proving-time measurement instead, so this fails loudly rather than
running with the wrong assumption.

Run standalone to check without starting a sweep:
    python3 check_setup.py
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "devnet" / "lib"))
import config  # noqa: E402

# keccak256("VERIFIER()")[:4] — computed once via `ethers.id("VERIFIER()")`,
# not re-derived at runtime to avoid a JS/web3 dependency in this checker.
VERIFIER_SELECTOR = "0x08c84e70"

# proof-api's SP1Config variants (packages/proof-api/modules/cosmos-to-eth/
# src/lib.rs). "mock" produces empty proof bytes; every other variant produces
# a real proof, so only "mock" pairs with SP1MockVerifier.
MOCK_PROVERS = {"mock"}
REAL_PROVERS = {"cpu", "cuda", "network", "env"}


class SetupError(RuntimeError):
    """A precondition failed. The message is meant to be read by a human."""


def _eth_call(rpc_url: str, to: str, data: str) -> str:
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "eth_call",
        "params": [{"to": to, "data": data}, "latest"],
    }).encode()
    req = urllib.request.Request(
        rpc_url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        out = json.load(r)
    if "error" in out:
        raise SetupError(f"eth_call to {to} failed: {out['error']}")
    return out["result"]


def _eth_get_code(rpc_url: str, address: str) -> str:
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "eth_getCode",
        "params": [address, "latest"],
    }).encode()
    req = urllib.request.Request(
        rpc_url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        out = json.load(r)
    if "error" in out:
        raise SetupError(f"eth_getCode({address}) failed: {out['error']}")
    return out["result"]


def check_cosmos_reachable(cfg) -> int:
    try:
        r = subprocess.run(
            [cfg["PQCHAIND_BIN"], "status", "--node", cfg["CHAIN_NODE"]],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SetupError(f"cannot reach Cosmos chain at {cfg['CHAIN_NODE']}: {e}") from e
    if r.returncode != 0:
        raise SetupError(
            f"Cosmos chain not up (pqchaind status failed against {cfg['CHAIN_NODE']}): "
            f"{r.stderr[-300:] or r.stdout[-300:]}\n"
            f"Bring it up first — see devnet/README.md#bringing-up-a-devnet.")
    try:
        return int(json.loads(r.stdout)["sync_info"]["latest_block_height"])
    except (ValueError, KeyError) as e:
        raise SetupError(f"could not parse Cosmos status output: {e}") from e


def check_cosmos_light_client_active(cfg):
    client_id = cfg.get("COSMOS_CLIENT_ID")
    if not client_id:
        raise SetupError(
            "COSMOS_CLIENT_ID is not set (should come from deploy.env after "
            "create-light-client.sh). Devnet is not fully up.")
    try:
        r = subprocess.run(
            [cfg["PQCHAIND_BIN"], "query", "ibc", "client", "status", client_id,
             "--node", cfg["CHAIN_NODE"], "-o", "json"],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SetupError(f"could not query Cosmos light client {client_id}: {e}") from e
    if r.returncode != 0:
        raise SetupError(
            f"Cosmos-side light client {client_id} query failed: "
            f"{r.stderr[-300:] or r.stdout[-300:]}\n"
            f"Run devnet/scripts/create-light-client.sh first.")
    status = (r.stdout or "").strip().strip('"')
    if "Active" not in status:
        raise SetupError(
            f"Cosmos-side light client {client_id} is not Active (status: {status!r}). "
            f"It may need updating — run "
            f"'python3 devnet/relayer/update-eth-client.py {client_id}'.")


def check_eth_devnet_reachable(cfg) -> int:
    rpc = cfg.get("GETH_RPC")
    if not rpc:
        raise SetupError(
            "GETH_RPC is not set. It is read from $DEVNET_DIR/ports.env, which "
            "devnet/scripts/write-ports-env.sh generates — the Ethereum devnet "
            "may not be up.")
    rpc_url = rpc if rpc.startswith("http") else f"http://{rpc}"
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": [],
    }).encode()
    try:
        req = urllib.request.Request(
            rpc_url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            out = json.load(r)
    except Exception as e:
        raise SetupError(
            f"cannot reach Ethereum devnet JSON-RPC at {rpc_url}: {e}\n"
            f"Bring it up first — see devnet/README.md#bringing-up-a-devnet.") from e
    if "error" in out:
        raise SetupError(f"eth_blockNumber failed: {out['error']}")
    return int(out["result"], 16)


def bound_verifier_kind(cfg) -> tuple[str, str]:
    """Return (kind, address) for the verifier the EVM light client is bound
    to, where kind is "mock" or "real". Raises SetupError if it is neither."""
    rpc = cfg.get("GETH_RPC")
    rpc_url = rpc if rpc.startswith("http") else f"http://{rpc}"
    sp1_ics07 = cfg.get("SP1_ICS07")
    bound = _eth_call(rpc_url, sp1_ics07, VERIFIER_SELECTOR)
    bound_addr = "0x" + bound[-40:]
    mock = (cfg.get("SP1_VERIFIER_MOCK") or "").lower()
    groth16 = (cfg.get("SP1_VERIFIER_GROTH16") or "").lower()
    if mock and bound_addr.lower() == mock:
        return "mock", bound_addr
    if groth16 and bound_addr.lower() == groth16:
        return "real", bound_addr
    raise SetupError(
        f"The EVM-side light client at {sp1_ics07} is bound to {bound_addr}, "
        f"which matches neither SP1_VERIFIER_MOCK nor SP1_VERIFIER_GROTH16 in "
        f"deploy.env.")


def _running_proof_api_config_path(addr: str) -> tuple[Path | None, str]:
    """Find the config file the RUNNING proof-api actually loaded.

    proof-api has no endpoint that reports its prover — its Info RPC exposes
    only program vkeys — so the closest obtainable truth is the --config path
    on the live process's own command line, read from /proc. That is stronger
    than trusting the file a bring-up script last wrote, which may have been
    rewritten after proof-api started. Returns (path, how) where `how`
    describes the provenance for the caller's message; path is None if no
    proof-api process could be identified.
    """
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            argv = (proc / "cmdline").read_bytes().split(b"\0")
        except (OSError, PermissionError):
            continue
        args = [a.decode("utf-8", "replace") for a in argv if a]
        if not args or "proof-api" not in args[0]:
            continue
        for i, a in enumerate(args):
            if a == "--config" and i + 1 < len(args):
                return Path(args[i + 1]), f"the running proof-api (pid {proc.name})"
            if a.startswith("--config="):
                return Path(a.split("=", 1)[1]), f"the running proof-api (pid {proc.name})"
    return None, ""


def check_proof_api_prover_matches_verifier(cfg) -> tuple[str, str]:
    """Fail if proof-api's prover and the on-chain verifier disagree.

    Returns (prover_type, verifier_kind) on success.
    """
    addr = cfg.get("PROOF_API_ADDR")
    if not addr:
        raise SetupError("PROOF_API_ADDR is not set in devnet.env.")
    host, _, port = addr.rpartition(":")
    try:
        with socket.create_connection((host or "127.0.0.1", int(port)), timeout=5):
            pass
    except OSError as e:
        raise SetupError(
            f"proof-api is not listening on {addr} ({e}). Start it — see "
            f"devnet/scripts/bring-up-native-asset.sh — then re-run.") from e

    cfg_path, how = _running_proof_api_config_path(addr)
    if cfg_path is None:
        cfg_path = Path(cfg["DEVNET_DIR"]) / "proof-api-config.json"
        how = (f"{cfg_path} (could NOT identify the running proof-api process, "
               f"so this is the on-disk config, which may differ from what the "
               f"live process loaded)")
    if not cfg_path.exists():
        raise SetupError(
            f"proof-api is listening on {addr} but its config {cfg_path} "
            f"(from {how}) does not exist, so its prover cannot be verified.")

    try:
        api_cfg = json.loads(cfg_path.read_text())
    except (OSError, ValueError) as e:
        raise SetupError(f"could not read proof-api config {cfg_path}: {e}") from e

    provers = set()
    for module in api_cfg.get("modules", []):
        sp1 = (module.get("config", {}).get("mode", {}) or {}).get("sp1")
        if sp1:
            provers.add(str((sp1.get("sp1_prover") or {}).get("type", "")).lower())
    if not provers:
        raise SetupError(
            f"proof-api config {cfg_path} declares no SP1 module, so no prover "
            f"could be read. This experiment needs the SP1 cosmos_to_eth module.")
    if len(provers) > 1:
        raise SetupError(
            f"proof-api config {cfg_path} declares more than one prover type "
            f"({', '.join(sorted(provers))}). Cannot pair an ambiguous prover "
            f"with a single on-chain verifier.")
    prover = provers.pop()

    verifier_kind, verifier_addr = bound_verifier_kind(cfg)
    expected = "mock" if verifier_kind == "mock" else "a real prover (%s)" % "/".join(sorted(REAL_PROVERS))
    ok = (prover in MOCK_PROVERS) if verifier_kind == "mock" else (prover in REAL_PROVERS)
    if ok:
        return prover, verifier_kind

    if verifier_kind == "mock":
        consequence = (
            "SP1MockVerifier's entire body is `assert(proofBytes.length == 0)`. "
            "A real prover produces a ~356-byte Groth16 proof, so EVERY relay "
            "transaction will revert with Panic(0x01) after paying full "
            "proving time and full calldata gas.")
        fix = ('set sp1_prover to {"type": "mock"} — re-run '
               'devnet/scripts/bring-up-native-asset.sh --verifier=mock, which '
               'now sets both sides from the one flag')
    else:
        consequence = (
            "SP1VerifierGroth16 verifies a real proof. A mock prover produces "
            "empty proof bytes, so EVERY relay transaction will revert.")
        fix = ('set sp1_prover to {"type": "cpu"} — re-run '
               'devnet/scripts/bring-up-native-asset.sh --verifier=real, which '
               'now sets both sides from the one flag')
    raise SetupError(
        f"PROVER/VERIFIER MISMATCH — these must be one setting, not two.\n"
        f"  on-chain verifier : {verifier_kind} ({verifier_addr})\n"
        f"  proof-api prover  : {prover}  (from {how}: {cfg_path})\n"
        f"  expected prover   : {expected}\n"
        f"{consequence}\n"
        f"Fix: {fix}, then restart proof-api.")


def check_eth_light_client_uses_mock_verifier(cfg):
    rpc = cfg.get("GETH_RPC")
    rpc_url = rpc if rpc.startswith("http") else f"http://{rpc}"
    sp1_ics07 = cfg.get("SP1_ICS07")
    verifier_mock = cfg.get("SP1_VERIFIER_MOCK")
    verifier_groth16 = cfg.get("SP1_VERIFIER_GROTH16")
    if not sp1_ics07:
        raise SetupError(
            "SP1_ICS07 is not set (should come from deploy.env after "
            "create-eth-client.js). The EVM-side light client does not exist yet.")
    if not verifier_mock:
        raise SetupError(
            "SP1_VERIFIER_MOCK is not set (should come from deploy.env after "
            "deploy-contracts.sh). Cannot confirm which verifier is bound.")

    code = _eth_get_code(rpc_url, sp1_ics07)
    if code in ("0x", "0x0", None):
        raise SetupError(
            f"SP1ICS07Tendermint at {sp1_ics07} (SP1_ICS07) has no code on this "
            f"chain. Either the EVM devnet was restarted (its state does not "
            f"survive a restart — see devnet/README.md) or the client was never "
            f"created. Run devnet/create-eth-client.js.")

    bound = _eth_call(rpc_url, sp1_ics07, VERIFIER_SELECTOR)
    bound_addr = "0x" + bound[-40:]

    if verifier_groth16 and bound_addr.lower() == verifier_groth16.lower():
        raise SetupError(
            f"The EVM-side light client at {sp1_ics07} is bound to the REAL "
            f"SP1VerifierGroth16 ({bound_addr}), not SP1MockVerifier "
            f"({verifier_mock}).\n"
            f"This test assumes the mock verifier — real proving costs ~10 "
            f"minutes per proof (see devnet/README.md#proving) and would turn "
            f"every group-size cell into a proving-time measurement instead of "
            f"a transfer-mechanism measurement.\n"
            f"A client cannot be switched between verifiers after creation. "
            f"Create a new client bound to the mock verifier instead — see "
            f"devnet/deploy/README.md#verifier-paths.")

    if bound_addr.lower() != verifier_mock.lower():
        raise SetupError(
            f"The EVM-side light client at {sp1_ics07} is bound to {bound_addr}, "
            f"which matches neither SP1_VERIFIER_MOCK ({verifier_mock}) nor "
            f"SP1_VERIFIER_GROTH16 ({verifier_groth16 or '(unset)'}) in deploy.env. "
            f"deploy.env may be stale relative to the live chain — check "
            f"whether the EVM devnet was redeployed. See devnet/README.md#the-evm-"
            f"devnet-does-not-survive-a-restart.")


def run_all(cfg, log=print) -> None:
    """Raises SetupError on the first failed precondition."""
    log("checking Cosmos chain is reachable...")
    height = check_cosmos_reachable(cfg)
    log(f"  ok, height {height}")

    log("checking Cosmos-side light client is Active...")
    check_cosmos_light_client_active(cfg)
    log("  ok")

    log("checking Ethereum devnet is reachable...")
    eth_height = check_eth_devnet_reachable(cfg)
    log(f"  ok, block {eth_height}")

    log("checking EVM-side light client is bound to SP1MockVerifier...")
    check_eth_light_client_uses_mock_verifier(cfg)
    log("  ok — SP1MockVerifier confirmed bound")

    log("checking proof-api's prover matches that verifier...")
    prover, verifier_kind = check_proof_api_prover_matches_verifier(cfg)
    log(f"  ok — prover '{prover}' pairs with the {verifier_kind} verifier")


def main():
    cfg = config.load()
    try:
        run_all(cfg)
    except SetupError as e:
        print(f"\nSETUP CHECK FAILED: {e}\n", file=sys.stderr)
        sys.exit(1)
    print("\nall preconditions satisfied — devnet is ready for the batch-scaling sweep")


if __name__ == "__main__":
    main()
