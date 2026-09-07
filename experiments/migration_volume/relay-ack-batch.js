// RETURN LEG of the Ethereum -> Cosmos migration: carry the acknowledgement
// back from Cosmos to Ethereum, closing the packets out.
//
// The packet travelled EVM -> Cosmos, but the ACK travels Cosmos -> EVM, so
// proof-api's src/dst — which describe the RELAY direction — are the reverse
// of the packet's. Same convention as devnet/step-native-ack.js.
//
// proof-api batches natively: given ONE Cosmos transaction carrying N
// acknowledgements (because N MsgRecvPacket were delivered together), RelayByTx
// returns ONE multicall of N ackPacket calls, and there is no separate
// updateClient entry — the client update is fused into its
// update_client_and_membership program. So one delivery transaction on Cosmos
// becomes one Ethereum transaction here, whatever N is.
//
// What this measures depends on which SP1 verifier the EVM light client is
// bound to, and that is fixed when the client is created:
//   mock  the MECHANISM — multicall batching, per-ack gas, calldata size.
//         Proof verification is a no-op, so proving time is NOT measured.
//   real  adds Groth16 verification on chain and ~10 min of proving per
//         request.
// The binding is recorded in the summary so a run can never be misread.
//
// Usage: node relay-ack-batch.js <cosmos-recv-txhash> --label=NAME [--expect=N]
//                                [--dry-run]
// Writes $DEVNET_DIR/migration-volume/ack-<label>.json
const fs = require("fs");
const path = require("path");
const { loadEnv, evm, ethers, config, sendRawTx } = require("../../devnet/lib/lib");
const proofapi = require("../../devnet/lib/proofapi");

const arg = (name, dflt) => {
  const a = process.argv.find((x) => x.startsWith(`--${name}=`));
  return a ? a.split("=").slice(1).join("=") : dflt;
};

(async () => {
  const recvTx = process.argv[2];
  const label = arg("label", null);
  const expect = parseInt(arg("expect", "0"), 10);
  const dryRun = process.argv.includes("--dry-run");
  if (!recvTx || !label) {
    console.error("usage: node relay-ack-batch.js <cosmos-recv-txhash> --label=NAME [--expect=N] [--dry-run]");
    process.exit(2);
  }

  const env = loadEnv();
  config.require_(env, "CHAIN_ID", "ETH_CLIENT_ID", "COSMOS_CLIENT_ID",
    "PROOF_API_ADDR", "ICS26_ROUTER", "SP1_ICS07");
  const { provider, router, lc } = evm(env);
  const chainId = (await provider.getNetwork()).chainId.toString();

  // Which verifier the client is bound to decides what these numbers mean, so
  // read it from the contract rather than from configuration.
  let verifier = null, verifierMode = "unknown";
  try {
    verifier = await lc.VERIFIER();
    const mock = (env.SP1_VERIFIER_MOCK || "").toLowerCase();
    const real = (env.SP1_VERIFIER_GROTH16 || "").toLowerCase();
    if (verifier.toLowerCase() === mock) verifierMode = "mock";
    else if (verifier.toLowerCase() === real) verifierMode = "real";
  } catch { /* older client without the accessor */ }

  console.log(`ack "${label}": relaying ${recvTx.slice(0, 16)}… Cosmos -> EVM`);
  console.log(`  verifier: ${verifierMode} (${verifier})`);

  const client = proofapi.connect(env);
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
  console.log(`  proof-api: ${relay.tx.length} bytes in ${proveSeconds.toFixed(1)}s`);
  if (relay.address.toLowerCase() !== env.ICS26_ROUTER.toLowerCase()) {
    throw new Error(`proof-api targets ${relay.address}, expected ICS26Router ${env.ICS26_ROUTER}`);
  }

  // --- decode: how many acks did one request actually carry? ---------------
  const sel = (name) => router.interface.getFunction(name).selector;
  const selectors = {
    [sel("ackPacket")]: "ackPacket",
    [sel("recvPacket")]: "recvPacket",
    [sel("multicall")]: "multicall",
  };
  const topSel = data.slice(0, 10);
  const counts = {};
  let inner = [];
  if (selectors[topSel] === "multicall") {
    [inner] = router.interface.decodeFunctionData("multicall", data);
    for (const c of inner) {
      const name = selectors[c.slice(0, 10)] || c.slice(0, 10);
      counts[name] = (counts[name] || 0) + 1;
    }
  } else {
    counts[selectors[topSel] || topSel] = 1;
  }
  const ackCount = counts.ackPacket || 0;
  console.log(`  ${selectors[topSel] || topSel} carrying ${ackCount} ackPacket call(s)`);
  if (expect && ackCount !== expect) {
    throw new Error(`expected ${expect} ackPacket call(s), proof-api produced ${ackCount} — ` +
      `the ack leg did not batch this delivery as assumed`);
  }

  const summary = {
    label, recvTx, verifierMode, verifier,
    relayBytes: relay.tx.length, proveSeconds,
    topLevel: selectors[topSel] || topSel,
    innerCallCount: inner.length, counts, ackCount,
  };

  if (!dryRun) {
    const block = await provider.getBlock("latest");
    const gasLimit = block.gasLimit - block.gasLimit / 20n;
    const t1 = Date.now();
    const r = await sendRawTx(router.runner, relay.address, data, { gasLimit });
    summary.submitSeconds = (Date.now() - t1) / 1000;
    summary.txHash = r.hash;
    summary.status = r.status;
    summary.gasUsed = Number(r.gasUsed);
    summary.blockNumber = r.blockNumber;
    summary.ackedTs = Date.now() / 1000;
    console.log(`  submitted -> status ${r.status}, gas ${r.gasUsed} ` +
      `(${ackCount ? Math.round(Number(r.gasUsed) / ackCount).toLocaleString() : "?"} per ack), ` +
      `block ${r.blockNumber}`);
    if (r.status !== 1) throw new Error("ack relay reverted");
  }

  const outDir = path.join(env.DEVNET_DIR, "migration-volume");
  fs.mkdirSync(outDir, { recursive: true });
  // A dry run must not leave a file that makes this delivery look
  // acknowledged — the default label selection keys off exactly that.
  const f = path.join(outDir, dryRun ? `ack-dryrun-${label}.json` : `ack-${label}.json`);
  fs.writeFileSync(f, JSON.stringify(summary, null, 2));
  console.log(`  wrote ${f}`);
})().catch((e) => { console.error(e.message || e); process.exit(1); });
