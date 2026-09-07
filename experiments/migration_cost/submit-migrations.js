// PHASE 1 of the Ethereum -> Cosmos migration flow: N independent users each
// escrow TestERC20 via their OWN ICS20Transfer.sendTransfer call.
//
// Deliberately NOT ICS20Transfer.multicall: the scenario being modelled is
// many independent users migrating at the same time, each paying for and
// signing their own transaction. Batching them into one tx would model a
// single custodial service and would collapse exactly the per-user
// submission cost this is meant to measure.
//
// Every account submits concurrently under a pre-assigned nonce, so nothing
// serializes on nonce discovery.
//
// Receiver addresses: each user migrates to its OWN Cosmos account, derived
// deterministically from its EVM address (bech32.js), not from the single
// COSMOS_RECEIVER in devnet.env — the whole point is N distinct receivers.
//
// --per-user=K has each user submit K transfers (default 1) under
// pre-assigned CONSECUTIVE nonces, fired without awaiting each receipt, so a
// large cohort is not serialized on 12s block times. Receipts are collected
// after all sends are broadcast.
//
// --label=NAME fixes the output filename to send-<NAME>.json. A measurement
// run passes its own label so the harness can address the file it just
// produced by
// name. Without it the name carries a timestamp, and a caller picking "the
// newest send-*.json" races any file left behind by an earlier trial.
//
// --pool-offset=K takes accounts K..K+count from the pool instead of the
// first `count`, so flows running at the same time never share an account —
// two in-flight transactions from one account race for the same nonce.
//
// Usage: node submit-migrations.js [count] [amount-each] [--per-user=K]
//                                  [--label=NAME] [--pool-offset=K]
//                                  [--pool-file=NAME]
// Writes $DEVNET_DIR/migration-cost/send-<label>.json
const fs = require("fs");
const path = require("path");
const { loadEnv, evm, ethers, config, abi } = require("../../devnet/lib/lib");
const P = require("../../devnet/lib/packet");
const bech32 = require("./bech32");

const OUT_SUBDIR = "migration-cost";

// One Cosmos receiver per user, deterministic from the EVM address so a run
// is reproducible and a credited balance can be traced back to its user.
const receiverFor = (evmAddress) =>
  bech32.encode("cosmos", Buffer.from(evmAddress.slice(2), "hex"));

(async () => {
  // Positional arguments with any --flag removed, so flag order never matters.
  const POS = process.argv.slice(2).filter((a) => !a.startsWith("--"));
  const count = parseInt(POS[0] || "10", 10);
  const amount = BigInt(POS[1] || "2000");
  const perUserArg = process.argv.find((a) => a.startsWith("--per-user="));
  const perUser = perUserArg ? parseInt(perUserArg.split("=")[1], 10) : 1;
  const labelArg = process.argv.find((a) => a.startsWith("--label="));
  const label = labelArg ? labelArg.split("=").slice(1).join("=") : null;
  // Concurrent flows must not share EVM accounts: two in-flight transactions
  // from one account race for the same nonce. Each flow takes its own slice.
  const offArg = process.argv.find((a) => a.startsWith("--pool-offset="));
  const poolOffset = offArg ? parseInt(offArg.split("=")[1], 10) : 0;
  const env = loadEnv();
  config.require_(env, "TEST_ERC20", "ICS20_TRANSFER", "ICS26_ROUTER", "ETH_CLIENT_ID");
  const { provider, router } = evm(env);

  const poolFileArg = (process.argv.find((a) => a.startsWith("--pool-file=")) || "").split("=")[1];
  const poolFile = path.join(env.DEVNET_DIR, poolFileArg || "evm-user-pool.json");
  if (!fs.existsSync(poolFile)) throw new Error(`no user pool: run 'node setup-user-pool.js ${count}' first`);
  const pool = JSON.parse(fs.readFileSync(poolFile, "utf8"));
  if (pool.length < poolOffset + count) {
    throw new Error(`user pool has ${pool.length} accounts, need ${poolOffset + count} ` +
      `(offset ${poolOffset} + count ${count})`);
  }
  const users = pool.slice(poolOffset, poolOffset + count);

  const outDir = path.join(env.DEVNET_DIR, OUT_SUBDIR);
  fs.mkdirSync(outDir, { recursive: true });
  const runId = Date.now();
  const outName = `send-${label || runId}.json`;

  const transferAbi = abi("ICS20Transfer");
  const timeout = BigInt(Math.floor(Date.now() / 1000) + 7200);

  // Pre-assign every nonce BEFORE any send, so no send waits on an RPC
  // round-trip that another send could have been using.
  const nonces = await Promise.all(
    users.map((u) => provider.getTransactionCount(u.address, "pending")));

  const total = count * perUser;
  console.log(`submitting ${total} sendTransfer call(s): ${count} user(s) x ${perUser} each (amount ${amount})...`);
  const t0 = Date.now();

  // Broadcast everything first (pre-assigned consecutive nonces per user),
  // then collect receipts. Awaiting each receipt inline would serialize a
  // user's transfers on 12s block times.
  const pending = [];
  await Promise.all(users.map(async (u, i) => {
    const w = new ethers.Wallet(u.privateKey, provider);
    const transfer = new ethers.Contract(env.ICS20_TRANSFER, transferAbi, w);
    const receiver = receiverFor(u.address);
    for (let j = 0; j < perUser; j++) {
      const rec = {
        index: u.index, subIndex: j, sender: u.address, receiver,
        amount: amount.toString(), submitted_ts: null, committed_ts: null,
        status: "pending", error: "",
      };
      pending.push(rec);
      try {
        rec.submitted_ts = Date.now() / 1000;
        const tx = await transfer.sendTransfer({
          denom: env.TEST_ERC20, amount, receiver,
          sourceClient: env.ETH_CLIENT_ID, destPort: "transfer",
          timeoutTimestamp: timeout, memo: "",
        }, { nonce: nonces[i] + j, gasLimit: 3_000_000 });
        rec.txHash = tx.hash;
        rec._tx = tx;
      } catch (e) {
        rec.status = "failed";
        rec.error = (e.message || String(e)).slice(0, 200);
      }
    }
  }));
  const broadcast = pending.filter((r) => r._tx).length;
  console.log(`  broadcast ${broadcast}/${total} in ${((Date.now() - t0) / 1000).toFixed(1)}s; collecting receipts...`);

  let done = 0;
  await Promise.all(pending.filter((r) => r._tx).map(async (rec) => {
    try {
      const r = await rec._tx.wait();
      rec.committed_ts = Date.now() / 1000;
      if (r.status !== 1) { rec.status = "failed"; rec.error = "sendTransfer reverted"; return; }
      rec.blockNumber = r.blockNumber;
      rec.sendGas = r.gasUsed.toString();
      let packet = null;
      for (const log of r.logs) {
        try {
          const parsed = router.interface.parseLog(log);
          if (parsed && parsed.name === "SendPacket") packet = parsed.args.packet;
        } catch { /* not a router event */ }
      }
      if (!packet) { rec.status = "failed"; rec.error = "no SendPacket event"; return; }
      const pkt = {
        sequence: BigInt(packet.sequence),
        sourceClient: packet.sourceClient,
        destClient: packet.destClient,
        timeoutTimestamp: BigInt(packet.timeoutTimestamp),
        payloads: packet.payloads.map((p) => ({
          sourcePort: p.sourcePort, destPort: p.destPort,
          version: p.version, encoding: p.encoding, value: p.value,
        })),
      };
      rec.sequence = pkt.sequence.toString();
      rec.sourceClient = pkt.sourceClient;
      rec.destClient = pkt.destClient;
      rec.timeoutTimestamp = pkt.timeoutTimestamp.toString();
      rec.payload = pkt.payloads[0];
      rec.commitment = P.packetCommitment(pkt);
      rec.status = "committed";
    } catch (e) {
      rec.status = "failed";
      rec.error = (e.message || String(e)).slice(0, 200);
    } finally {
      if (++done % 100 === 0) console.log(`    ${done}/${broadcast} receipts`);
    }
  }));
  for (const r of pending) delete r._tx;
  const results = pending;
  const elapsed = (Date.now() - t0) / 1000;

  const ok = results.filter((r) => r.status === "committed");
  const blocks = [...new Set(ok.map((r) => r.blockNumber))].sort((a, b) => a - b);
  const totalGas = ok.reduce((a, r) => a + BigInt(r.sendGas), 0n);
  const failures = results.filter((r) => r.status !== "committed");
  for (const r of failures.slice(0, 5)) console.log(`  FAILED [${r.index}.${r.subIndex}] ${r.error}`);
  if (failures.length > 5) console.log(`  ... and ${failures.length - 5} more failures`);
  const seqs = ok.map((r) => Number(r.sequence)).sort((a, b) => a - b);
  console.log(`\n${ok.length}/${results.length} committed in ${elapsed.toFixed(1)}s ` +
    `across ${blocks.length} block(s) [${blocks[0]}..${blocks[blocks.length - 1]}]`);
  console.log(`sequences ${seqs[0]}..${seqs[seqs.length - 1]}`);
  console.log(`submission gas total ${totalGas} (mean ${ok.length ? totalGas / BigInt(ok.length) : 0n} per user)`);

  const outFile = path.join(outDir, outName);
  fs.writeFileSync(outFile, JSON.stringify({
    runId, label, count, perUser, poolOffset, amountEach: amount.toString(),
    sourceClient: env.ETH_CLIENT_ID, destClient: env.COSMOS_CLIENT_ID,
    elapsedSeconds: elapsed,
    firstSubmittedTs: Math.min(...results.filter((r) => r.submitted_ts).map((r) => r.submitted_ts)),
    lastCommittedTs: Math.max(...ok.map((r) => r.committed_ts)),
    committed: ok.length,
    blocks, submitGasTotal: totalGas.toString(),
    packets: results,
  }, null, 2));
  console.log(`wrote ${outFile}`);
})().catch((e) => { console.error(e.message || e); process.exit(1); });
