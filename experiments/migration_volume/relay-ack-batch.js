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
//                                [--dry-run] [--tx-max-size=B] [--chunk-margin=F]
//                                [--sender-pool=FILE --sender-index=N]
// Writes $DEVNET_DIR/migration-volume/ack-<label>.json
const fs = require("fs");
const path = require("path");
const { loadEnv, evm, ethers, config, sendRawTx } = require("../../devnet/lib/lib");
const { captureRevert } = require("../../devnet/lib/revert");
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
  // Acks can run concurrently, but only from DIFFERENT Ethereum accounts: two
  // in-flight transactions from one account race for the same nonce. Relaying
  // is permissionless (ackPacket behaves identically from any sender), so a
  // pool of ordinary funded accounts is enough.
  const senderPool = arg("sender-pool", null);
  const senderIndex = parseInt(arg("sender-index", "0"), 10);
  if (!recvTx || !label) {
    console.error("usage: node relay-ack-batch.js <cosmos-recv-txhash> --label=NAME [--expect=N] [--dry-run]");
    process.exit(2);
  }

  const env = loadEnv();
  config.require_(env, "CHAIN_ID", "ETH_CLIENT_ID", "COSMOS_CLIENT_ID",
    "PROOF_API_ADDR", "ICS26_ROUTER", "SP1_ICS07");

  // Refuse to acknowledge the same delivery twice. Packets already
  // acknowledged do not revert -- the calls simply do nothing -- so a second
  // run reports a far lower gas figure that looks like a real measurement and
  // overwrites the real one. --force is there for a deliberate re-measurement.
  const outDirEarly = path.join(env.DEVNET_DIR, "migration-volume");
  const existing = path.join(outDirEarly, `ack-${label}.json`);
  if (!dryRun && !process.argv.includes("--force") && fs.existsSync(existing)) {
    throw new Error(`${label} is already acknowledged (${existing}). ` +
      `Re-acknowledging measures nothing: the packets are closed, the calls ` +
      `no-op, and the gas figure would be wrong. Pass --force to overwrite.`);
  }
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

  let sender = router.runner;                       // the deployer by default
  if (senderPool) {
    const poolPath = path.join(env.DEVNET_DIR, senderPool);
    const pool = JSON.parse(fs.readFileSync(poolPath, "utf8"));
    const entry = pool[senderIndex];
    if (!entry) throw new Error(`${poolPath} has ${pool.length} account(s), need index ${senderIndex}`);
    sender = new ethers.Wallet(entry.privateKey, provider);
  }

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

  // --- split to fit geth's per-transaction size limit ----------------------
  // The forward leg is walled by CometBFT's 4 MB max_tx_bytes; this direction
  // is walled by geth's txMaxSize, 128 KB — over 30x smaller, so the ack leg
  // runs out of room roughly an order of magnitude sooner than the delivery it
  // is answering. proof-api returns the whole multicall regardless, so the
  // split happens here: each group is re-encoded as its own multicall, which
  // is valid because every ackPacket carries its own fused proof.
  const txMaxSize = parseInt(arg("tx-max-size", "131072"), 10);
  const margin = parseFloat(arg("chunk-margin", "0.90"));
  const budget = Math.floor(txMaxSize * margin);

  // geth's limit applies to the SIGNED, RLP-encoded transaction, not to the
  // calldata inside it, so the size is measured by signing a candidate rather
  // than scaling the calldata length. Nothing is broadcast to measure it.
  const feeData = await provider.getFeeData();
  const nonce = await provider.getTransactionCount(await sender.getAddress(), "pending");
  const netId = (await provider.getNetwork()).chainId;
  const signedSize = async (payload) => {
    const raw = await sender.signTransaction({
      to: relay.address, data: payload, nonce, chainId: netId, type: 2,
      gasLimit: 30_000_000n,
      maxFeePerGas: feeData.maxFeePerGas ?? 10n ** 9n,
      maxPriorityFeePerGas: feeData.maxPriorityFeePerGas ?? 10n ** 9n,
    });
    return (raw.length - 2) / 2;
  };
  const wholeSize = await signedSize(data);

  // THE MULTICALL CANNOT BE SPLIT. proof-api fuses one
  // update_client_and_membership proof over every packet in the request;
  // SP1ICS07Tendermint verifies those key/value pairs once and caches them FOR
  // THE DURATION OF THE TRANSACTION. Re-encoding a subset as its own multicall
  // therefore fails on the second transaction with
  //
  //   SP1ICS07Tendermint.KeyValuePairNotInCache(...)
  //
  // because the pairs its acks refer to were verified in a transaction that
  // has already ended. Proving and acknowledging must share one transaction.
  //
  // So this is a HARD ceiling, unlike the forward leg's: there, every
  // MsgRecvPacket carries its own proof and a batch splits freely across
  // transactions. Here the only way to acknowledge more packets is to have
  // DELIVERED them in smaller Cosmos transactions, because the ack batch is
  // whatever one delivery produced.
  if (wholeSize > budget) {
    const perAck = ackCount ? wholeSize / ackCount : wholeSize;
    const fits = Math.max(1, Math.floor(budget / perAck));
    throw new Error(
      `ack for ${ackCount} packet(s) does not fit: signed transaction is ` +
      `${wholeSize.toLocaleString()} B against geth's txMaxSize ` +
      `${txMaxSize.toLocaleString()} B (${budget.toLocaleString()} B usable at ` +
      `margin ${margin}). At ~${Math.round(perAck).toLocaleString()} B per ack ` +
      `the ceiling is ~${fits} acks per transaction, and the multicall CANNOT be ` +
      `split — the membership proof is cached only within its own transaction. ` +
      `Deliver in batches of <= ~${fits} if the packets must be acknowledged.`);
  }
  console.log(`  signed tx ${wholeSize.toLocaleString()} B of ${budget.toLocaleString()} B usable`);
  const groups = [null];   // always exactly one transaction

  const summary = {
    label, recvTx, verifierMode, verifier,
    relayBytes: relay.tx.length, proveSeconds,
    topLevel: selectors[topSel] || topSel,
    innerCallCount: inner.length, counts, ackCount,
    txMaxSize, signedBytes: wholeSize, chunks: 1,
    sender: await sender.getAddress(),
  };

  if (!dryRun) {
    const block = await provider.getBlock("latest");
    const gasLimit = block.gasLimit - block.gasLimit / 20n;
    const t1 = Date.now();
    const parts = [];
    for (let gi = 0; gi < groups.length; gi++) {
      const payload = data;
      let r;
      try {
        r = await sendRawTx(sender, relay.address, payload, { gasLimit });
      } catch (e) {
        // A receipt alone says status=0 and nothing else. Re-execute the same
        // call against the block it failed in and decode the revert data.
        const rc = e.receipt;
        const why = rc ? await captureRevert(provider,
          { from: await sender.getAddress(), to: relay.address, data: payload },
          rc.blockNumber) : null;
        throw new Error(`ack chunk ${gi} (${groups[gi] ? groups[gi].length : ackCount} acks) failed: ` +
          `${why ? why.text : (e.shortMessage || e.message)}`);
      }
      if (r.status !== 1) {
        const why = await captureRevert(provider,
          { from: await sender.getAddress(), to: relay.address, data: payload },
          r.blockNumber);
        throw new Error(`ack chunk ${gi} (${groups[gi] ? groups[gi].length : ackCount} acks) ` +
          `reverted at block ${r.blockNumber}: ${why.text}`);
      }
      parts.push({
        chunk: gi,
        acks: groups[gi] === null ? ackCount : groups[gi].length,
        txHash: r.hash, status: r.status,
        gasUsed: Number(r.gasUsed), blockNumber: r.blockNumber,
        bytes: (payload.length - 2) / 2,
      });

    }
    summary.submitSeconds = (Date.now() - t1) / 1000;
    summary.parts = parts;
    summary.txHash = parts[parts.length - 1].txHash;
    summary.status = 1;
    summary.gasUsed = parts.reduce((a, p) => a + p.gasUsed, 0);
    summary.submittedBytes = parts.reduce((a, p) => a + p.bytes, 0);
    summary.blockNumber = parts[parts.length - 1].blockNumber;
    summary.ackedTs = Date.now() / 1000;
    console.log(`  submitted -> gas ${summary.gasUsed.toLocaleString()} ` +
      `(${ackCount ? Math.round(summary.gasUsed / ackCount).toLocaleString() : "?"} per ack) ` +
      `over ${parts.length} transaction(s)`);
  }

  const outDir = path.join(env.DEVNET_DIR, "migration-volume");
  fs.mkdirSync(outDir, { recursive: true });
  // A dry run must not leave a file that makes this delivery look
  // acknowledged — the default label selection keys off exactly that.
  const f = path.join(outDir, dryRun ? `ack-dryrun-${label}.json` : `ack-${label}.json`);
  fs.writeFileSync(f, JSON.stringify(summary, null, 2));
  console.log(`  wrote ${f}`);
})().catch((e) => { console.error(e.message || e); process.exit(1); });
