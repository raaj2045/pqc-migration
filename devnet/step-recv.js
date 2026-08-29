// Cosmos -> EVM leg: relay the packet to the EVM side with a real SP1 proof.
//
// Usage: node step-recv.js <cosmos-tx-hash>
//
// The EVM-side client (SP1ICS07Tendermint) is bound to the real
// SP1VerifierGroth16, so the client update and the packet membership proof are
// both cryptographically checked on chain. Both are produced by proof-api,
// which returns a single multicall for the ICS26Router — this script no longer
// assembles SP1 proof structs itself. Proving is CPU-bound and takes minutes.
//
// Requires a running proof-api; see PROOF_API_ADDR in devnet.env.example.
const fs = require("fs");
const path = require("path");
const { loadEnv, evm, sendRawTx, cosmosCli, config } = require("./lib/lib");
const proofapi = require("./lib/proofapi");
const P = require("./lib/packet");

const TXHASH = process.argv[2];
if (!TXHASH) {
  console.error("usage: node step-recv.js <cosmos-tx-hash>");
  process.exit(2);
}

function cosmosTx(hash) {
  return JSON.parse(cosmosCli(["query", "tx", hash, "-o", "json"]));
}

(async () => {
  const env = loadEnv();
  config.require_(env, "CHAIN_ID", "ETH_CLIENT_ID", "COSMOS_CLIENT_ID", "PROOF_API_ADDR");
  const { provider, router, transfer } = evm(env);

  // 1. Pull the packet out of the send_packet event.
  const tx = cosmosTx(TXHASH);
  const ev = tx.events.find((e) => e.type === "send_packet");
  const hex = ev.attributes.find((a) => a.key === "encoded_packet_hex").value;
  const pkt = P.decodePacket(hex);
  const sendHeight = BigInt(tx.height);
  console.log(`packet: seq=${pkt.sequence} ${pkt.sourceClient} -> ${pkt.destClient} sent at cosmos height ${sendHeight}`);
  console.log(`payload: ${pkt.payloads[0].sourcePort}/${pkt.payloads[0].destPort} ${pkt.payloads[0].version} ${pkt.payloads[0].encoding}`);

  // The relay request filters events by client id; a mismatch here would
  // silently yield an empty relay tx rather than an obvious failure.
  if (pkt.sourceClient !== env.COSMOS_CLIENT_ID || pkt.destClient !== env.ETH_CLIENT_ID) {
    throw new Error(
      `packet clients (${pkt.sourceClient} -> ${pkt.destClient}) do not match configured ` +
      `COSMOS_CLIENT_ID/ETH_CLIENT_ID (${env.COSMOS_CLIENT_ID} -> ${env.ETH_CLIENT_ID})`
    );
  }

  // 2. Cross-check our commitment against what the chain actually stored.
  const commitment = P.packetCommitment(pkt);
  const stored = JSON.parse(cosmosCli(["query", "ibc", "channelv2", "packet-commitment",
    pkt.sourceClient, String(pkt.sequence), "-o", "json"]));
  const storedHex = "0x" + Buffer.from(stored.commitment, "base64").toString("hex");
  if (storedHex !== commitment) throw new Error(`commitment mismatch: computed ${commitment} stored ${storedHex}`);
  console.log(`commitment: ${commitment} (matches on-chain state)`);

  // 3. Ask proof-api for the relay multicall. This is where the SP1 proof is
  //    generated: minutes of CPU work, not a round trip.
  const chainId = (await provider.getNetwork()).chainId.toString();
  const client = proofapi.connect(env);
  console.log(`requesting relay tx from proof-api at ${env.PROOF_API_ADDR} (proving, expect minutes)...`);
  const started = Date.now();
  const relay = await proofapi.relayByTx(client, {
    srcChain: env.CHAIN_ID,
    dstChain: chainId,
    sourceTxIds: [Buffer.from(TXHASH, "hex")],
    srcClientId: pkt.sourceClient,
    dstClientId: pkt.destClient,
  });
  const proveSeconds = ((Date.now() - started) / 1000).toFixed(1);
  console.log(`relay tx: ${relay.tx.length} bytes for ${relay.address} (proved in ${proveSeconds}s)`);

  if (relay.address.toLowerCase() !== env.ICS26_ROUTER.toLowerCase()) {
    throw new Error(`proof-api targets ${relay.address}, expected ICS26Router ${env.ICS26_ROUTER}`);
  }

  // 4. Submit it. The multicall carries the client update and the packet
  //    message; the light client verifies both against the Groth16 verifier.
  const data = "0x" + Buffer.from(relay.tx).toString("hex");
  const r = await sendRawTx(router.runner, relay.address, data, { gasLimit: 15_000_000 });
  console.log(`relay -> status ${r.status}, gas ${r.gasUsed}, block ${r.blockNumber}`);
  if (r.status !== 1) throw new Error("relay tx reverted");

  // 5. Surface the write acknowledgement the router emitted.
  let ackHex = null;
  for (const log of r.logs) {
    try {
      const parsed = router.interface.parseLog(log);
      if (parsed && parsed.name === "WriteAcknowledgement") {
        ackHex = parsed.args.acknowledgements[0];
        console.log(`WriteAcknowledgement: ${Buffer.from(ackHex.slice(2), "hex").toString()}`);
      }
    } catch { /* not a router event */ }
  }
  if (!ackHex) throw new Error("no WriteAcknowledgement emitted");

  const denom = `${pkt.payloads[0].destPort}/${pkt.destClient}/stake`;
  const erc20 = await transfer.ibcERC20Contract(denom).catch(() => null);
  const outPath = process.env.RECV_RESULT_PATH
    || path.join(env.DEVNET_DIR, "recv-result.json");
  fs.writeFileSync(outPath, JSON.stringify({
    sequence: String(pkt.sequence), commitment,
    ack: ackHex, recvGas: String(r.gasUsed), ackBlockNumber: String(r.blockNumber), denom, erc20,
    packetHex: hex, proveSeconds, relayTxBytes: relay.tx.length,
  }, null, 2));
  console.log(`ibc denom: ${denom} -> ${erc20}`);
  console.log(`wrote ${outPath}`);
})();
