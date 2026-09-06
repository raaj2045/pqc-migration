// PHASE 0, UNKNOWN 1: does proof-api batch acknowledgements?
//
// On this direction the ack travels Cosmos -> EVM, and is produced by
// proof-api exactly as the FORWARD leg of the other direction is. The open
// question is whether, given ONE Cosmos tx hash that contains N
// acknowledgements (because N MsgRecvPacket were batched into it), RelayByTx
// returns:
//
//   (a) one multicall containing N ackPacket calls  -> the ack leg batches,
//       and the whole flow amortizes end to end; or
//   (b) a single ackPacket for one packet only      -> the ack leg is
//       inherently per-packet and must be pooled, not batched.
//
// This answers it by DECODING the returned calldata rather than trusting
// documentation: count the ackPacket selectors inside the multicall.
//
// Deliberately does not broadcast. The question is what proof-api builds;
// submitting it would also consume the packets and prevent re-running.
//
// Usage: node probe-ack-batching.js <cosmos-recv-txhash> [--submit]
const fs = require("fs");
const path = require("path");
const { loadEnv, evm, ethers, config, sendRawTx } = require("../../devnet/lib/lib");
const proofapi = require("../../devnet/lib/proofapi");

(async () => {
  const recvTx = process.argv[2];
  const doSubmit = process.argv.includes("--submit");
  if (!recvTx) {
    console.error("usage: node probe-ack-batching.js <cosmos-recv-txhash> [--submit]");
    process.exit(2);
  }
  const env = loadEnv();
  config.require_(env, "CHAIN_ID", "ETH_CLIENT_ID", "COSMOS_CLIENT_ID", "PROOF_API_ADDR", "ICS26_ROUTER");
  const { provider, router } = evm(env);
  const chainId = (await provider.getNetwork()).chainId.toString();

  // The packet travelled EVM -> Cosmos, but the ACK travels Cosmos -> EVM,
  // so proof-api's src/dst (which describe the RELAY direction) are the
  // reverse of the packet's — same convention as step-native-ack.js.
  const client = proofapi.connect(env);
  console.log(`RelayByTx: src=${env.CHAIN_ID}/${env.COSMOS_CLIENT_ID} -> dst=${chainId}/${env.ETH_CLIENT_ID}`);
  console.log(`  source tx: ${recvTx}`);
  const t0 = Date.now();
  const relay = await proofapi.relayByTx(client, {
    srcChain: env.CHAIN_ID,
    dstChain: chainId,
    sourceTxIds: [Buffer.from(recvTx, "hex")],
    srcClientId: env.COSMOS_CLIENT_ID,
    dstClientId: env.ETH_CLIENT_ID,
  });
  const proveSeconds = (Date.now() - t0) / 1000;
  const data = "0x" + Buffer.from(relay.tx).toString("hex");
  console.log(`  returned ${relay.tx.length} bytes for ${relay.address} in ${proveSeconds.toFixed(1)}s`);
  if (relay.address.toLowerCase() !== env.ICS26_ROUTER.toLowerCase()) {
    throw new Error(`proof-api targets ${relay.address}, expected ICS26Router ${env.ICS26_ROUTER}`);
  }

  // --- decode ---------------------------------------------------------------
  const sel = (name) => router.interface.getFunction(name).selector;
  const selectors = {
    [sel("ackPacket")]: "ackPacket",
    [sel("recvPacket")]: "recvPacket",
    [sel("multicall")]: "multicall",
  };
  const topSel = data.slice(0, 10);
  console.log(`  top-level selector: ${topSel} (${selectors[topSel] || "unknown"})`);

  const counts = {};
  let inner = [];
  if (selectors[topSel] === "multicall") {
    [inner] = router.interface.decodeFunctionData("multicall", data);
    for (const c of inner) {
      const s = c.slice(0, 10);
      const name = selectors[s] || s;
      counts[name] = (counts[name] || 0) + 1;
    }
    console.log(`  multicall with ${inner.length} inner call(s):`);
    for (const [k, v] of Object.entries(counts)) console.log(`    ${k}: ${v}`);
  } else {
    counts[selectors[topSel] || topSel] = 1;
    console.log("  NOT a multicall — a single top-level call");
  }

  const ackCount = counts["ackPacket"] || 0;
  console.log(`\n  ==> proof-api produced ${ackCount} ackPacket call(s) from this tx`);

  const outDir = path.join(env.DEVNET_DIR, "migration-volume");
  fs.mkdirSync(outDir, { recursive: true });
  const summary = {
    recvTx, relayBytes: relay.tx.length, proveSeconds,
    topLevel: selectors[topSel] || topSel,
    innerCallCount: inner.length, counts, ackPacketCalls: ackCount,
  };

  if (doSubmit) {
    const block = await provider.getBlock("latest");
    const gasLimit = block.gasLimit - block.gasLimit / 20n;
    const r = await sendRawTx(router.runner, relay.address, data, { gasLimit });
    summary.submitted = { txHash: r.hash, status: r.status, gasUsed: r.gasUsed.toString(), blockNumber: r.blockNumber };
    console.log(`  submitted -> status ${r.status}, gas ${r.gasUsed}, block ${r.blockNumber}`);
    summary.ack_ts = Date.now() / 1000;
  }

  const f = path.join(outDir, `ack-probe-${recvTx.slice(0, 12)}.json`);
  fs.writeFileSync(f, JSON.stringify(summary, null, 2));
  console.log(`  wrote ${f}`);
})().catch((e) => { console.error(e.message || e); process.exit(1); });
