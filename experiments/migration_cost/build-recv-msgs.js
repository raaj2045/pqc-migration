// Build (never broadcast) the full MsgRecvPacket array for a set of packets,
// so find_recv_ceiling.py can slice it to any N, sign, and measure the real
// encoded transaction size.
//
// Separated from relay-recv-batch.js because the ceiling search needs to
// build ONCE and then re-sign many slices — re-running the proof fetch per
// bisection step would be wasted work, and re-fetching at a moving finalized
// block would change the thing being measured mid-search.
//
// The `signer` field is written as a placeholder; the caller rewrites it per
// signing key (the key type under test), since `tx sign` rejects a tx whose
// declared signer does not match the signing key.
//
// Usage: node build-recv-msgs.js <send-file> --count=N [--offset=K]
//                                --out=<path> [--no-update]
const fs = require("fs");
const { execFileSync } = require("child_process");
const { loadEnv, evm, ethers, config } = require("../../devnet/lib/lib");
const P = require("../../devnet/lib/packet");

const arg = (n, d) => {
  const a = process.argv.find((x) => x.startsWith(`--${n}=`));
  return a ? a.split("=").slice(1).join("=") : d;
};
const b64 = (hex) => Buffer.from(hex.replace(/^0x/, ""), "hex").toString("base64");
const get = (u) => JSON.parse(execFileSync("curl", ["-s", "-m", "30", u], { maxBuffer: 256e6 }));

(async () => {
  const sendFile = process.argv[2];
  const count = parseInt(arg("count", "0"), 10);
  const offset = parseInt(arg("offset", "0"), 10);
  const out = arg("out", null);
  if (!sendFile || !count || !out) {
    console.error("usage: node build-recv-msgs.js <send-file> --count=N [--offset=K] --out=<path> [--no-update]");
    process.exit(2);
  }
  const env = loadEnv();
  config.require_(env, "COSMOS_CLIENT_ID", "ICS26_ROUTER", "IBC_COMMITMENT_SLOT",
    "UPDATE_CLIENT_CMD", "RELAYER_KEY", "BEACON_URL", "GETH_RPC");
  const { router } = evm(env);

  const send = JSON.parse(fs.readFileSync(sendFile, "utf8"));
  const batch = send.packets.filter((p) => p.status === "committed").slice(offset, offset + count);
  if (batch.length !== count) throw new Error(`only ${batch.length} packets at offset ${offset}, need ${count}`);

  const slotHex = ethers.zeroPadValue(env.IBC_COMMITMENT_SLOT, 32);
  const keys = batch.map((p) =>
    ethers.keccak256(ethers.concat([
      ethers.keccak256(P.packetCommitmentKey(p.sourceClient, BigInt(p.sequence))), slotHex])));

  // finality must cover the newest send in the set
  const maxSendBlock = Math.max(...batch.map((p) => p.blockNumber));
  const beacon = env.BEACON_URL.replace(/\/$/, "");
  let hdr;
  for (let i = 0; i < 120; i++) {
    const fin = get(`${beacon}/eth/v1/beacon/light_client/finality_update`).data;
    const n = Number(fin.finalized_header.execution.block_number);
    if (n >= maxSendBlock) { hdr = fin.finalized_header; break; }
    console.error(`  waiting for finality to cover block ${maxSendBlock} (at ${n})`);
    execFileSync("sleep", ["12"]);
  }
  if (!hdr) throw new Error("finality never advanced past the send block");
  const proofSlot = Number(hdr.beacon.slot);

  const cli = (a) => JSON.parse(execFileSync(env.PQCHAIND_BIN,
    [...a, "--home", env.CHAIN_HOME, "--node", env.CHAIN_NODE, "-o", "json"],
    { encoding: "utf8", maxBuffer: 256e6 }));
  if (process.argv.includes("--force-update")) {
    const [c, ...a] = env.UPDATE_CLIENT_CMD.split(/\s+/);
    execFileSync(c, [...a, env.COSMOS_CLIENT_ID], { stdio: "inherit" });
  }
  // A consensus state only needs to be at or after the send block. Demanding
  // one at the CURRENT finality slot races a beacon that keeps advancing, and
  // rejects a client perfectly able to prove the packet.
  const execBlockOf = (slot) =>
    Number(get(`${beacon}/eth/v2/beacon/blocks/${slot}`).data.message.body.execution_payload.block_number);
  let have = cli(["query", "ibc", "client", "consensus-states", env.COSMOS_CLIENT_ID])
    .consensus_states.map((e) => Number(e.height.revision_height)).sort((a, b) => a - b);
  // Prefer the NEWEST held state that covers the send block, not the oldest:
  // geth keeps only ~128 blocks of historical state (TriesInMemory), so an
  // older consensus state is likely to name a block whose state has been
  // pruned, and eth_getProof then returns nothing ("historical state ... not
  // available"). Newest-first keeps the proof inside geth's window.
  let useSlot = null;
  for (const s of [...have].reverse()) {
    if (s >= proofSlot || execBlockOf(s) >= maxSendBlock) { useSlot = s; break; }
  }
  if (useSlot === null) {
    const [c, ...a] = env.UPDATE_CLIENT_CMD.split(/\s+/);
    execFileSync(c, [...a, env.COSMOS_CLIENT_ID], { stdio: "inherit" });
    have = cli(["query", "ibc", "client", "consensus-states", env.COSMOS_CLIENT_ID])
      .consensus_states.map((e) => Number(e.height.revision_height)).sort((a, b) => a - b);
    for (const s of [...have].reverse()) {
      if (execBlockOf(s) >= maxSendBlock) { useSlot = s; break; }
    }
  }
  if (useSlot === null) throw new Error(`no consensus state covering block ${maxSendBlock}; have [${have.join(",")}]`);
  const useBlock = execBlockOf(useSlot);

  // one eth_getProof for every key
  const rpc = (env.GETH_RPC || "").replace(/^https?:\/\//, "");
  const t0 = Date.now();
  const proof = JSON.parse(execFileSync("curl", [
    "-s", "-m", "300", "-X", "POST", "-H", "Content-Type: application/json",
    "--data", JSON.stringify({
      jsonrpc: "2.0", method: "eth_getProof",
      params: [env.ICS26_ROUTER, keys, "0x" + useBlock.toString(16)], id: 1,
    }), `http://${rpc}`,
  ], { maxBuffer: 512e6 })).result;
  const proofSeconds = (Date.now() - t0) / 1000;
  if (!proof || !proof.storageProof || proof.storageProof.length !== count) {
    throw new Error(`eth_getProof returned ${proof && proof.storageProof ? proof.storageProof.length : "no"} proofs, expected ${count}`);
  }
  const empty = proof.storageProof.filter((sp) => sp.value === "0x0").length;
  if (empty) throw new Error(`${empty} storage value(s) are 0x0 at block ${useBlock} — already received or not committed`);

  const msgs = batch.map((p, i) => {
    const sp = proof.storageProof[i];
    return {
      "@type": "/ibc.core.channel.v2.MsgRecvPacket",
      packet: {
        sequence: p.sequence, source_client: p.sourceClient,
        destination_client: p.destClient, timeout_timestamp: p.timeoutTimestamp,
        payloads: [{
          source_port: p.payload.sourcePort, destination_port: p.payload.destPort,
          version: p.payload.version, encoding: p.payload.encoding,
          value: b64(p.payload.value),
        }],
      },
      proof_commitment: Buffer.from(JSON.stringify({
        account_proof: { storage_root: proof.storageHash, proof: proof.accountProof },
        storage_proof: { key: ethers.zeroPadValue(sp.key, 32), value: sp.value, proof: sp.proof },
      })).toString("base64"),
      proof_height: { revision_number: "0", revision_height: String(useSlot) },
      signer: "PLACEHOLDER",
    };
  });
  fs.writeFileSync(out, JSON.stringify(msgs));
  console.error(`built ${count} msgs at slot ${useSlot} (block ${useBlock}) in ${proofSeconds.toFixed(1)}s -> ${out}`);
  console.log(JSON.stringify({ count, useSlot, useBlock, proofSeconds,
    accountProofBytes: proof.accountProof.join("").length / 2,
    sequences: batch.map((p) => p.sequence) }));
})().catch((e) => { console.error(e.message || e); process.exit(1); });
