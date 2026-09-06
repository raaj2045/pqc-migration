// Measures the on-wire size of a relay-batch transaction for a given set of
// Cosmos tx hashes, WITHOUT broadcasting it — a pure size probe used by
// find_relay_ceiling.py to bisect for the real packets-per-tx ceiling under
// geth's 128KB (131072-byte) default tx-size cap (see
// find_relay_ceiling.py's docstring for the go-ethereum reference: issue
// #23920, and README.md's "Relay chunk size" section).
//
// Measures the SIGNED, RLP-serialized transaction — what geth's txMaxSize
// check actually applies to — not just the multicall calldata, since the
// signature and envelope fields add a small but real amount on top of
// calldata length.
//
// Usage: node probe-relay-size.js <cosmos-tx-hash> [<cosmos-tx-hash> ...]
const { loadEnv, evm, config } = require("../../devnet/lib/lib");
const proofapi = require("../../devnet/lib/proofapi");

(async () => {
  const txHashes = process.argv.slice(2);
  if (txHashes.length === 0) {
    console.error("usage: node probe-relay-size.js <cosmos-tx-hash> [<cosmos-tx-hash> ...]");
    process.exit(2);
  }
  const env = loadEnv();
  config.require_(env, "CHAIN_ID", "ETH_CLIENT_ID", "COSMOS_CLIENT_ID", "PROOF_API_ADDR");
  const { provider, router } = evm(env);
  const chainId = (await provider.getNetwork()).chainId.toString();
  const client = proofapi.connect(env);

  const relay = await proofapi.relayByTx(client, {
    srcChain: env.CHAIN_ID,
    dstChain: chainId,
    sourceTxIds: txHashes.map((h) => Buffer.from(h, "hex")),
    srcClientId: env.COSMOS_CLIENT_ID,
    dstClientId: env.ETH_CLIENT_ID,
  });
  const data = "0x" + Buffer.from(relay.tx).toString("hex");

  // Sign but do not send: read-only nonce lookup, no broadcast, safe to call
  // repeatedly against the same still-unrelayed packets during a bisection.
  const from = await router.runner.getAddress();
  const [nonce, feeData, network] = await Promise.all([
    provider.getTransactionCount(from, "pending"),
    provider.getFeeData(),
    provider.getNetwork(),
  ]);
  const signed = await router.runner.signTransaction({
    to: relay.address,
    data,
    nonce,
    chainId: network.chainId,
    gasLimit: 60_000_000n,
    maxFeePerGas: feeData.maxFeePerGas,
    maxPriorityFeePerGas: feeData.maxPriorityFeePerGas,
    type: 2,
  });

  console.log(JSON.stringify({
    count: txHashes.length,
    calldataBytes: data.length / 2 - 1,
    signedTxBytes: (signed.length - 2) / 2,
  }));
})().catch((e) => {
  console.error((e && e.stack) || String(e));
  process.exit(1);
});
