// Return leg (EVM -> Cosmos), verified by the REAL Ethereum light client.
//
// Usage: node step-ack.js [recv-result.json] [--signer-key=<keyring key name>]
//
// Builds an eth_getProof membership proof of the acknowledgement stored in
// ICS26Router and submits MsgAcknowledgement, which cw-ics08-wasm-eth verifies
// against the execution state root it holds for a finalized slot.
//
// This is the leg where genuine consensus verification happens: it waits for
// real Ethereum finality to cover the acknowledgement's execution block, and
// the MsgUpdateClient it triggers BLS-verifies the 512-key sync committee.
// The forward leg, by contrast, is checked by a MOCK SP1 verifier on this
// devnet. See experiments/migration_throughput/README.md.
//
// --signer-key defaults to RELAYER_KEY (devnet.env, itself defaulting to
// "validator"). Passing a different keyring key (e.g. from ack_pool.py's
// concurrent pool workers) makes THAT account sign and pay for both this
// MsgAcknowledgement and the MsgUpdateClient it may trigger — the msg
// "signer" address is resolved from the given key, not a fixed config entry, since a Cosmos tx is rejected if its declared signer address
// doesn't match whoever actually signed it.
const fs = require("fs");
const { execFileSync } = require("child_process");
const path = require("path");
const { loadEnv, evm, cosmosCli, ethers, signerAddress } = require("./lib/lib");
const P = require("./lib/packet");

// Everything below resolves through the shared config layer; this script holds
// no path, port or address of its own.
const ENV = loadEnv();
const argv = process.argv.slice(2);
const signerArg = argv.find((a) => a.startsWith("--signer-key="));
const SIGNER_KEY = (signerArg && signerArg.split("=")[1]) || ENV.RELAYER_KEY || "validator";
const RECV_RESULT = argv.find((a) => !a.startsWith("--signer-key="))
  || path.join(ENV.DEVNET_DIR, "recv-result.json");

const beaconUrl = () => (ENV.BEACON_URL || ENV.BEACON).replace(/\/$/, "");
const signer = () => signerAddress(ENV, SIGNER_KEY);

const b64 = (hex) => Buffer.from(hex.replace(/^0x/, ""), "hex").toString("base64");
const get = (url) => JSON.parse(execFileSync("curl", ["-s", url], { maxBuffer: 64e6 }));

const cli = (args) => cosmosCli([...args, "-o", "json"]);

(async () => {
  const env = loadEnv();
  const { provider, router } = evm(env);
  const recv = JSON.parse(fs.readFileSync(RECV_RESULT));
  const pkt = P.decodePacket(recv.packetHex);

  // 1. The ack the EVM wrote and its commitment, cross-checked on-chain.
  const ackHex = recv.ack;
  const ackComm = P.ackCommitment([ackHex]);
  const ackPath = P.packetAckKey(pkt.destClient, pkt.sequence);
  const stored = await router.getCommitment(ethers.keccak256(ackPath));
  if (stored !== ackComm) throw new Error(`ack commitment mismatch: ${ackComm} vs ${stored}`);
  console.log(`ack           : ${Buffer.from(ackHex.slice(2), "hex").toString()}`);
  console.log(`ack commitment: ${ackComm} (matches EVM state)`);

  // 2. Storage slot of that commitment: keccak256(keccak256(path) || slot),
  //    the standard Solidity mapping layout the light client recomputes.
  const slotHex = ethers.zeroPadValue(env.IBC_COMMITMENT_SLOT || "0x1260944489272988d9df285149b5aa1b0f48f2136d6f416159f840a3e0747600", 32);
  const storageKey = ethers.keccak256(ethers.concat([ethers.keccak256(ackPath), slotHex]));
  console.log(`storage key   : ${storageKey}`);

  // 3. Find a finalized slot whose execution block already contains the ack.
  const ackBlock = Number(recv.ackBlockNumber);
  let hdr;
  for (let i = 0; i < 60; i++) {
    const fin = get(`${beaconUrl()}/eth/v1/beacon/light_client/finality_update`).data;
    const execNum = Number(fin.finalized_header.execution.block_number);
    if (execNum >= ackBlock) { hdr = fin.finalized_header; break; }
    console.log(`  waiting for finality to cover block ${ackBlock} (at ${execNum})`);
    execFileSync("sleep", ["12"]);
  }
  if (!hdr) throw new Error("finality never advanced past the ack block");
  const proofSlot = Number(hdr.beacon.slot);
  const proofBlock = Number(hdr.execution.block_number);
  console.log(`proof slot    : ${proofSlot} (execution block ${proofBlock})`);

  // 4. Wait until the light client actually holds a consensus state at or
  //    after that slot, rather than assuming one update call produced it.
  //
  //    A single MsgUpdateClient is not enough under a concurrent ack pool.
  //    Every worker runs this same step against the ONE shared client, so a
  //    worker's own update can lose the race in several ordinary ways: it
  //    reverts because a peer landed the same header first, it is still in
  //    the mempool when the query below runs, or the header it submitted was
  //    already superseded. Failing optimistically on the first query turns
  //    all of those into a hard packet failure ("no consensus state at or
  //    after slot N"), which is what killed seq 0/596/635 in the last run —
  //    the `have` lists in those errors were filling up as the failures
  //    happened, i.e. the state was arriving, just not yet.
  //
  //    So: poll, and re-trigger an update periodically while polling.
  //    - POLL_S = 12s, one Ethereum slot — the finest granularity at which
  //      new beacon data can exist, so polling faster only burns queries.
  //    - UPDATE_EVERY = 3 polls (36s) — long enough for a submitted update
  //      to be included in a Cosmos block and for a peer's update to become
  //      visible, so we re-submit only when nothing landed, instead of
  //      paying for a redundant 512-key BLS verification every 12s.
  //    - WAIT_S = 480s (8 min) — one Ethereum finality epoch is ~6.4 min
  //      (2 epochs, 64 slots), so this covers the worst honest case: the
  //      finality update we read in step 3 was superseded and we must wait
  //      for the next finalization to be provable again, plus tx queueing
  //      behind 25 concurrent workers. It stays well inside ack_pool.py's
  //      2400s per-packet subprocess timeout, so a genuinely stuck client
  //      still surfaces as this explicit error rather than as a timeout.
  const POLL_S = 12;
  const UPDATE_EVERY = 3;
  const WAIT_S = 480;
  const updParts = ENV.UPDATE_CLIENT_CMD.split(" ");
  const updateClient = () => {
    try {
      execFileSync(updParts[0],
        [...updParts.slice(1), env.COSMOS_CLIENT_ID, `--signer-key=${SIGNER_KEY}`],
        { stdio: "inherit" });
    } catch (e) {
      // Not fatal: a concurrent worker may have landed the same header first.
      // Whatever actually made it on-chain is what the next poll reads.
      console.log(`  update-client attempt failed (${String(e.message).split("\n")[0]}) `
        + `— continuing to poll`);
    }
  };
  const consensusHeights = () => JSON.parse(
    cli(["query", "ibc", "client", "consensus-states", env.COSMOS_CLIENT_ID])
  ).consensus_states.map((e) => Number(e.height.revision_height));

  const deadline = Date.now() + WAIT_S * 1000;
  let useSlot = null;
  let have = [];
  for (let attempt = 0; ; attempt++) {
    if (attempt % UPDATE_EVERY === 0) updateClient();
    have = consensusHeights();
    const usable = have.filter((s) => s >= proofSlot);
    if (usable.length) { useSlot = Math.max(...usable); break; }
    const left = Math.round((deadline - Date.now()) / 1000);
    if (left <= 0) break;
    console.log(`  waiting for a consensus state at or after slot ${proofSlot} `
      + `(have ${have.length ? have.join(",") : "none"}; ${left}s left)`);
    execFileSync("sleep", [String(POLL_S)]);
  }
  if (!useSlot || !Number.isFinite(useSlot)) {
    throw new Error(`no consensus state at or after slot ${proofSlot} after ${WAIT_S}s `
      + `of waiting and periodic client updates; have ${have}`);
  }
  // Re-derive the execution block for the slot we will actually prove against.
  const blk = get(`${beaconUrl()}/eth/v2/beacon/blocks/${useSlot}`);
  const useBlock = Number(blk.data.message.body.execution_payload.block_number);
  console.log(`proving at    : slot ${useSlot}, execution block ${useBlock}`);

  // 5. eth_getProof at that execution block.
  // Use curl rather than the ethers provider: its keep-alive socket goes stale
  // across the multi-minute wait for finality and the next call ECONNRESETs.
  const rpcUrl = (ENV.GETH_RPC || "").replace(/^https?:\/\//, "");
  const proof = JSON.parse(execFileSync("curl", [
    "-s", "-m", "60", "-X", "POST", "-H", "Content-Type: application/json",
    "--data", JSON.stringify({
      jsonrpc: "2.0", method: "eth_getProof",
      params: [env.ICS26_ROUTER, [storageKey], "0x" + useBlock.toString(16)], id: 1,
    }),
    `http://${rpcUrl}`,
  ], { maxBuffer: 64e6 })).result;
  const sp = proof.storageProof[0];
  console.log(`storage value : ${sp.value}`);

  const membershipProof = {
    account_proof: { storage_root: proof.storageHash, proof: proof.accountProof },
    storage_proof: {
      key: ethers.zeroPadValue(sp.key, 32),
      value: sp.value,
      proof: sp.proof,
    },
  };

  // 6. MsgAcknowledgement, verified by 08-wasm-1 against its execution root.
  const payload = pkt.payloads[0];
  const msg = {
    "@type": "/ibc.core.channel.v2.MsgAcknowledgement",
    packet: {
      sequence: String(pkt.sequence),
      source_client: pkt.sourceClient,
      destination_client: pkt.destClient,
      timeout_timestamp: String(pkt.timeoutTimestamp),
      payloads: [{
        source_port: payload.sourcePort,
        destination_port: payload.destPort,
        version: payload.version,
        encoding: payload.encoding,
        value: b64(payload.value),
      }],
    },
    acknowledgement: { app_acknowledgements: [b64(ackHex)] },
    proof_acked: Buffer.from(JSON.stringify(membershipProof)).toString("base64"),
    proof_height: { revision_number: "0", revision_height: String(useSlot) },
    signer: signer(),
  };
  const msgPath = path.join(ENV.DEVNET_DIR,
    `msg-ack-wasm-${pkt.sourceClient}-${pkt.sequence}.json`);
  fs.writeFileSync(msgPath, JSON.stringify(msg, null, 2));

  const txParts = ENV.SENDTX_CMD.split(" ");
  const out = execFileSync(txParts[0],
    [...txParts.slice(1), msgPath, SIGNER_KEY, "3000000"],
    { encoding: "utf8" });
  console.log(out.trim());
  fs.unlinkSync(msgPath);
})();
