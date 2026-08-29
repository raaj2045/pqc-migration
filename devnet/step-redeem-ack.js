// Redemption, step 3 (EVM side): prove the Cosmos acknowledgement back to
// ICS26Router so the redemption packet is closed out.
//
// Usage: node step-redeem-ack.js <cosmos-recv-txhash>
//
// The acknowledgement membership proof and the client update behind it are
// real SP1 Groth16 proofs produced by proof-api and verified on chain by
// SP1VerifierGroth16 — the same path step-recv.js uses for the forward leg.
// Proving is CPU-bound and takes minutes.
const fs = require("fs");
const { execFileSync } = require("child_process");
const { loadEnv, evm, sendRawTx, ethers, config } = require("./lib/lib");
const proofapi = require("./lib/proofapi");
const P = require("./lib/packet");

const RECV_TX = process.argv[2];
if (!RECV_TX) {
  console.error("usage: node step-redeem-ack.js <cosmos-recv-txhash>");
  process.exit(2);
}

(async () => {
  const env = loadEnv();
  config.require_(env, "CHAIN_ID", "ETH_CLIENT_ID", "COSMOS_CLIENT_ID", "PROOF_API_ADDR");
  const { provider, router } = evm(env);
  const send = JSON.parse(fs.readFileSync(env.file("redeem-send.json")));
  const seq = BigInt(send.sequence);

  const tx = JSON.parse(execFileSync(env.PQCHAIND_BIN,
    ["query", "tx", RECV_TX, "--home", env.CHAIN_HOME, "--node", env.CHAIN_NODE, "-o", "json"],
    { encoding: "utf8", maxBuffer: 64e6 }));
  if (tx.code !== 0) throw new Error(`cosmos recv tx failed with code ${tx.code}`);
  console.log(`cosmos wrote ack for seq ${seq} at height ${tx.height}`);

  // The redemption packet travels EVM -> Cosmos, but this relay travels
  // Cosmos -> EVM (it carries the acknowledgement home). proof-api's client ids
  // describe the relay direction, so they are the reverse of the packet's:
  // source is the Cosmos-side client, destination the EVM-side one.
  const chainId = (await provider.getNetwork()).chainId.toString();
  const client = proofapi.connect(env);
  console.log(`requesting relay tx from proof-api at ${env.PROOF_API_ADDR} (proving, expect minutes)...`);
  const started = Date.now();
  const relay = await proofapi.relayByTx(client, {
    srcChain: env.CHAIN_ID,
    dstChain: chainId,
    sourceTxIds: [Buffer.from(RECV_TX, "hex")],
    srcClientId: env.COSMOS_CLIENT_ID,
    dstClientId: env.ETH_CLIENT_ID,
  });
  const proveSeconds = ((Date.now() - started) / 1000).toFixed(1);
  console.log(`relay tx: ${relay.tx.length} bytes for ${relay.address} (proved in ${proveSeconds}s)`);

  if (relay.address.toLowerCase() !== env.ICS26_ROUTER.toLowerCase()) {
    throw new Error(`proof-api targets ${relay.address}, expected ICS26Router ${env.ICS26_ROUTER}`);
  }

  const data = "0x" + Buffer.from(relay.tx).toString("hex");
  const r = await sendRawTx(router.runner, relay.address, data, { gasLimit: 15_000_000 });
  console.log(`ackPacket relay -> status ${r.status}, gas ${r.gasUsed}, block ${r.blockNumber}`);
  if (r.status !== 1) throw new Error("relay tx reverted");

  // The EVM-side commitment for the outbound redemption packet is cleared once
  // the acknowledgement is accepted; that is what closes the packet out.
  const key = ethers.keccak256(P.packetCommitmentKey(send.sourceClient, seq));
  const after = await router.getCommitment(key);
  const cleared = after === ethers.ZeroHash;
  console.log(`packet commitment on EVM: ${cleared ? "CLEARED (ack processed)" : after}`);
  if (!cleared) throw new Error("ack relayed but packet commitment not cleared");

  fs.writeFileSync(env.file("redeem-ack.json"), JSON.stringify({
    sequence: String(seq), ackGas: String(r.gasUsed), ackBlockNumber: String(r.blockNumber),
    relayTxHash: r.hash, proveSeconds, relayTxBytes: relay.tx.length,
  }, null, 2));
  console.log(`wrote ${env.file("redeem-ack.json")}`);
})();
