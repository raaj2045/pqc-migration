// Cosmos -> EVM leg: update the EVM-side light client to the Cosmos height that
// commits the packet, then submit recvPacket with an SP1 membership proof.
//
// Usage: node step-recv.js <cosmos-tx-hash>
//
// NOTE ON THE VERIFIER: the EVM-side client (SP1ICS07Tendermint) runs a MOCK
// verifier on this devnet, so the membership proof is not cryptographically
// checked here. Real consensus verification on this bridge happens on the
// RETURN leg, where the Cosmos-side cw-ics08-wasm-eth client BLS-verifies the
// Ethereum sync committee. See devnet/README.md.
const fs = require("fs");
const path = require("path");
const {
  loadEnv, evm, sendTx, cosmosCli, cosmosHeader, waitCosmosHeight, coder, ethers,
  T_CONSENSUS_STATE, T_MEMBERSHIP_OUTPUT, T_SP1_PROOF, T_MEMBERSHIP_PROOF_INNER,
  T_UPDATE_OUTPUT, T_CLIENT_STATE,
} = require("./lib/lib");
const P = require("./lib/packet");

const TXHASH = process.argv[2];
if (!TXHASH) {
  console.error("usage: node step-recv.js <cosmos-tx-hash>");
  process.exit(2);
}

// Binary, home and node all come from the shared config layer, so this script
// has no knowledge of where the devnet lives.
function cosmosTx(hash) {
  return JSON.parse(cosmosCli(["query", "tx", hash, "-o", "json"]));
}

(async () => {
  const env = loadEnv();
  const { provider, router, lc, transfer } = evm(env);

  // 1. Pull the packet out of the send_packet event.
  const tx = cosmosTx(TXHASH);
  const ev = tx.events.find((e) => e.type === "send_packet");
  const hex = ev.attributes.find((a) => a.key === "encoded_packet_hex").value;
  const pkt = P.decodePacket(hex);
  const sendHeight = BigInt(tx.height);
  console.log(`packet: seq=${pkt.sequence} ${pkt.sourceClient} -> ${pkt.destClient} sent at cosmos height ${sendHeight}`);
  console.log(`payload: ${pkt.payloads[0].sourcePort}/${pkt.payloads[0].destPort} ${pkt.payloads[0].version} ${pkt.payloads[0].encoding}`);

  // 2. Cross-check our commitment against what the chain actually stored.
  const commitment = P.packetCommitment(pkt);
  const stored = JSON.parse(cosmosCli(["query", "ibc", "channelv2", "packet-commitment",
    pkt.sourceClient, String(pkt.sequence), "-o", "json"]));
  const storedHex = "0x" + Buffer.from(stored.commitment, "base64").toString("hex");
  if (storedHex !== commitment) throw new Error(`commitment mismatch: computed ${commitment} stored ${storedHex}`);
  console.log(`commitment: ${commitment} (matches on-chain state)`);

  // 3. The app hash at H+1 commits the state written during block H.
  const proofHeight = sendHeight + 1n;
  await waitCosmosHeight(Number(proofHeight));
  const hdr = await cosmosHeader(Number(proofHeight));
  const newConsensus = [hdr.timestampNanos, hdr.appHash, hdr.nextValidatorsHash];

  // 4. updateClient from the seeded trusted height to proofHeight.
  const trustedHdr = await cosmosHeader(Number(env.COSMOS_HEIGHT));
  const trustedConsensus = [trustedHdr.timestampNanos, trustedHdr.appHash, trustedHdr.nextValidatorsHash];
  const csNow = await lc.clientState();
  const clientStateTuple = [csNow[0], [csNow[1][0], csNow[1][1]], [csNow[2][0], csNow[2][1]],
    csNow[3], csNow[4], csNow[5], csNow[6]];

  // SP1ICS07 rejects a proof timestamped after block.timestamp
  // (ProofIsInTheFuture). With 6s blocks the local clock runs ahead of the
  // chain, so anchor on the chain's own latest block time.
  const latestBlock = await provider.getBlock("latest");
  const nowNanos = BigInt(latestBlock.timestamp) * 1000000000n;
  const updateOutput = coder.encode([T_UPDATE_OUTPUT], [[
    clientStateTuple, trustedConsensus, newConsensus, nowNanos,
    [1n, BigInt(env.COSMOS_HEIGHT)], [1n, proofHeight],
  ]]);
  const updateMsg = coder.encode([`(${T_SP1_PROOF})`], [[[ethers.ZeroHash, updateOutput, "0x"]]]);

  // Routed through ICS26Router: PROOF_SUBMITTER_ROLE on the light client is
  // held by the router, and updateClient is a public relayer selector.
  let r = await sendTx(router, "updateClient", [env.ETH_CLIENT_ID, updateMsg]);
  console.log(`updateClient -> height ${proofHeight}, gas ${r.gasUsed}`);

  // 5. Membership proof: the kv pair is the prefixed commitment path -> commitment.
  const merklePrefix = (await router.getCounterparty(env.ETH_CLIENT_ID)).merklePrefix;
  const rawPath = P.packetCommitmentKey(pkt.sourceClient, pkt.sequence);
  const fullPath = [...merklePrefix];
  fullPath[fullPath.length - 1] = ethers.concat([fullPath[fullPath.length - 1], rawPath]);

  const membershipOutput = coder.encode([T_MEMBERSHIP_OUTPUT],
    [[hdr.appHash, [[fullPath, commitment]]]]);
  const inner = coder.encode([T_MEMBERSHIP_PROOF_INNER],
    [[[ethers.ZeroHash, membershipOutput, "0x"], newConsensus]]);
  const proof = coder.encode(["(uint8,bytes)"], [[0, inner]]);

  // 6. recvPacket
  const msgRecv = [P.toSolidityPacket(pkt), proof, [1n, proofHeight]];
  r = await sendTx(router, "recvPacket", [msgRecv], { gasLimit: 5_000_000 });
  console.log(`recvPacket -> status ${r.status}, gas ${r.gasUsed}`);

  // 7. Surface the write acknowledgement the router emitted.
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
    sequence: String(pkt.sequence), commitment, proofHeight: String(proofHeight),
    ack: ackHex, recvGas: String(r.gasUsed), ackBlockNumber: String(r.blockNumber), denom, erc20,
    packetHex: hex,
  }, null, 2));
  console.log(`ibc denom: ${denom} -> ${erc20}`);
  console.log(`wrote ${outPath}`);
})();
