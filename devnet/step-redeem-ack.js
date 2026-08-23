// Redemption, step 3 (EVM side): prove the Cosmos acknowledgement back to
// ICS26Router so the redemption packet is closed out. Uses SP1ICS07 with the
// mock verifier, the same path the forward leg's recvPacket uses.
const fs = require("fs");
const { execFileSync } = require("child_process");
const {
  loadEnv, evm, sendTx, cosmosHeader, waitCosmosHeight, coder, ethers,
  T_MEMBERSHIP_OUTPUT, T_SP1_PROOF, T_MEMBERSHIP_PROOF_INNER, T_UPDATE_OUTPUT,
} = require("./lib/lib");
const P = require("./lib/packet");

const RECV_TX = process.argv[2];

// Acknowledgement bytes written by the Cosmos transfer module (ICS20 success).
const ACK = ethers.hexlify(ethers.toUtf8Bytes('{"result":"AQ=="}'));

(async () => {
  const env = loadEnv();
  const { provider, router, lc } = evm(env);
  const send = JSON.parse(fs.readFileSync(env.file("redeem-send.json")));
  const seq = BigInt(send.sequence);

  const tx = JSON.parse(execFileSync(env.PQCHAIND_BIN,
    ["query", "tx", RECV_TX, "--home", env.CHAIN_HOME, "--node", env.CHAIN_NODE, "-o", "json"],
    { encoding: "utf8", maxBuffer: 64e6 }));
  const ackHeight = BigInt(tx.height);
  console.log(`cosmos wrote ack at height ${ackHeight}`);

  // App hash at H+1 commits state written during block H.
  const proofHeight = ackHeight + 1n;
  await waitCosmosHeight(Number(proofHeight));
  const hdr = await cosmosHeader(Number(proofHeight));
  const newConsensus = [hdr.timestampNanos, hdr.appHash, hdr.nextValidatorsHash];

  const trustedHdr = await cosmosHeader(Number(env.COSMOS_HEIGHT));
  const trustedConsensus = [trustedHdr.timestampNanos, trustedHdr.appHash, trustedHdr.nextValidatorsHash];
  const csNow = await lc.clientState();
  const clientStateTuple = [csNow[0], [csNow[1][0], csNow[1][1]], [csNow[2][0], csNow[2][1]],
    csNow[3], csNow[4], csNow[5], csNow[6]];

  const latestBlock = await provider.getBlock("latest");
  const nowNanos = BigInt(latestBlock.timestamp) * 1000000000n;
  const updateOutput = coder.encode([T_UPDATE_OUTPUT], [[
    clientStateTuple, trustedConsensus, newConsensus, nowNanos,
    [1n, BigInt(env.COSMOS_HEIGHT)], [1n, proofHeight],
  ]]);
  const updateMsg = coder.encode([`(${T_SP1_PROOF})`], [[[ethers.ZeroHash, updateOutput, "0x"]]]);
  let r = await sendTx(router, "updateClient", [env.ETH_CLIENT_ID, updateMsg]);
  console.log(`updateClient -> cosmos height ${proofHeight}, gas ${r.gasUsed}`);

  // Membership of the Cosmos ack commitment, under the counterparty prefix.
  const ackCommitment = P.ackCommitment([ACK]);
  const merklePrefix = (await router.getCounterparty(env.ETH_CLIENT_ID)).merklePrefix;
  const rawPath = P.packetAckKey(send.destClient, seq);
  const fullPath = [...merklePrefix];
  fullPath[fullPath.length - 1] = ethers.concat([fullPath[fullPath.length - 1], rawPath]);

  const membershipOutput = coder.encode([T_MEMBERSHIP_OUTPUT],
    [[hdr.appHash, [[fullPath, ackCommitment]]]]);
  const inner = coder.encode([T_MEMBERSHIP_PROOF_INNER],
    [[[ethers.ZeroHash, membershipOutput, "0x"], newConsensus]]);
  const proof = coder.encode(["(uint8,bytes)"], [[0, inner]]);

  const packet = [
    seq, send.sourceClient, send.destClient, BigInt(send.timeoutTimestamp),
    [[send.payload.sourcePort, send.payload.destPort, send.payload.version,
      send.payload.encoding, send.payload.value]],
  ];
  r = await sendTx(router, "ackPacket", [[packet, ACK, proof, [1n, proofHeight]]],
    { gasLimit: 3_000_000 });
  console.log(`ackPacket -> status ${r.status}, gas ${r.gasUsed}`);

  const key = ethers.keccak256(P.packetCommitmentKey(send.sourceClient, seq));
  const after = await router.getCommitment(key);
  console.log(`packet commitment on EVM: ${after === ethers.ZeroHash ? "CLEARED (ack processed)" : after}`);

  fs.writeFileSync(env.file("redeem-ack.json"), JSON.stringify({
    ackCommitment, proofHeight: String(proofHeight), ackGas: String(r.gasUsed),
  }, null, 2));
})();
