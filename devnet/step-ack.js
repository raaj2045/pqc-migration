// Return leg (EVM -> Cosmos), verified by the REAL Ethereum light client.
//
// Usage: node step-ack.js [recv-result.json]
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
const fs = require("fs");
const { execFileSync } = require("child_process");
const path = require("path");
const { loadEnv, evm, cosmosCli, ethers } = require("./lib/lib");
const P = require("./lib/packet");

// Everything below resolves through the shared config layer; this script holds
// no path, port or address of its own.
const ENV = loadEnv();
const RECV_RESULT = process.argv[2]
  || path.join(ENV.DEVNET_DIR, "recv-result.json");

const beaconUrl = () => (ENV.BEACON_URL || ENV.BEACON).replace(/\/$/, "");
const validator = () => ENV.VALIDATOR;

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

  // 4. Make sure the light client holds a consensus state at that slot.
  const updParts = ENV.UPDATE_CLIENT_CMD.split(" ");
  execFileSync(updParts[0], [...updParts.slice(1), env.COSMOS_CLIENT_ID],
    { stdio: "inherit" });
  const states = JSON.parse(cli(["query", "ibc", "client", "consensus-states", env.COSMOS_CLIENT_ID]));
  const have = states.consensus_states.map((e) => Number(e.height.revision_height));
  const useSlot = have.includes(proofSlot) ? proofSlot : Math.max(...have.filter((s) => s >= proofSlot));
  if (!useSlot || !Number.isFinite(useSlot)) {
    throw new Error(`no consensus state at or after slot ${proofSlot}; have ${have}`);
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
    signer: validator(),
  };
  const msgPath = path.join(ENV.DEVNET_DIR,
    `msg-ack-wasm-${pkt.sourceClient}-${pkt.sequence}.json`);
  fs.writeFileSync(msgPath, JSON.stringify(msg, null, 2));

  const txParts = ENV.SENDTX_CMD.split(" ");
  const out = execFileSync(txParts[0],
    [...txParts.slice(1), msgPath, ENV.RELAYER_KEY || "validator", "3000000"],
    { encoding: "utf8" });
  console.log(out.trim());
  fs.unlinkSync(msgPath);
})();
