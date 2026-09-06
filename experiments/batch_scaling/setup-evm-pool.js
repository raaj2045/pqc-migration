// Create and fund a pool of EVM accounts for concurrent relay-chunk
// submission — the EVM-side analogue of setup_pool.py's Cosmos account pool.
//
// Why a separate pool, not just DEPLOYER_PK with sequential nonces: ethers
// CAN queue several sequentially-nonced sends from one signer without
// waiting for each to confirm (lib.js's nonces Map already does this), but
// that still means one account, one mempool queue, and every chunk racing
// through the SAME nonce sequence. Independent accounts genuinely broadcast
// and get included independently, which is what the pool-concurrency model
// used for Cosmos submission (Part 2) is being extended to here for
// relaying (Part 4's ask).
//
// Idempotent: reuses any account already in the pool file with sufficient
// balance; only creates/funds what's short.
//
// Usage: node setup-evm-pool.js <pool-size> [<eth-amount-each>]
//   <eth-amount-each> default: 10 (ETH)
const fs = require("fs");
const path = require("path");
const { loadEnv, ethers, config } = require("../../devnet/lib/lib");

(async () => {
  const poolSize = parseInt(process.argv[2], 10);
  const amountEach = process.argv[3] || "10";
  if (!poolSize || poolSize < 1) {
    console.error("usage: node setup-evm-pool.js <pool-size> [<eth-amount-each>]");
    process.exit(2);
  }
  const env = loadEnv();
  config.require_(env, "GETH_RPC", "DEPLOYER_PK");
  const url = env.GETH_RPC.startsWith("http") ? env.GETH_RPC : `http://${env.GETH_RPC}`;
  const provider = new ethers.JsonRpcProvider(url, undefined, { cacheTimeout: -1 });
  const deployer = new ethers.Wallet(env.DEPLOYER_PK, provider);
  const amountWei = ethers.parseEther(amountEach);

  const poolFile = path.join(env.DEVNET_DIR, "evm-relay-pool.json");
  let pool = [];
  if (fs.existsSync(poolFile)) {
    pool = JSON.parse(fs.readFileSync(poolFile, "utf8"));
  }

  // Wallet creation is local (no RPC) and synchronous — generate every
  // missing entry up front instead of interleaving it with the balance
  // checks below, so the awaits that follow are the only sequencing cost.
  for (let i = 0; i < poolSize; i++) {
    if (!pool[i]) {
      const wallet = ethers.Wallet.createRandom();
      pool[i] = { index: i, address: wallet.address, privateKey: wallet.privateKey };
      console.log(`created ${pool[i].address} (index ${i})`);
    }
  }

  // Balance checks are independent reads — fire them concurrently rather
  // than one round-trip per account.
  const entries = pool.slice(0, poolSize);
  const balances = await Promise.all(entries.map((e) => provider.getBalance(e.address)));

  // There's no EVM equivalent of Cosmos's MsgMultiSend here (no batch-
  // transfer/multicall contract deployed in this devnet) — funding an
  // address still costs its own top-level transaction. But since nonces
  // just need to be assigned in a fixed order, not sent one-at-a-time, we
  // can still submit every funding tx concurrently instead of waiting for
  // each submission before starting the next.
  let nonce = await provider.getTransactionCount(await deployer.getAddress(), "pending");
  const fundTxs = [];
  const sendPromises = [];
  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i];
    const bal = balances[i];
    if (bal < amountWei) {
      const amount = amountWei - bal;
      const txNonce = nonce++;
      sendPromises.push(
        deployer.sendTransaction({ to: entry.address, value: amount, nonce: txNonce }).then((tx) => {
          fundTxs.push({ entry, tx });
          console.log(`funding ${entry.address}: +${ethers.formatEther(amount)} ETH (tx ${tx.hash})`);
        })
      );
    } else {
      console.log(`${entry.address} already funded: ${ethers.formatEther(bal)} ETH`);
    }
  }
  if (sendPromises.length === 0) {
    console.log("all accounts already funded, skipping funding transactions");
  } else {
    await Promise.all(sendPromises);
    await Promise.all(fundTxs.map(async ({ entry, tx }) => {
      const r = await tx.wait();
      if (r.status !== 1) throw new Error(`funding tx for ${entry.address} reverted`);
    }));
  }

  fs.writeFileSync(poolFile, JSON.stringify(pool.slice(0, Math.max(pool.length, poolSize)), null, 2));
  console.log(`wrote ${pool.length} entries to ${poolFile}`);
})().catch((e) => {
  console.error(e.message || e);
  process.exit(1);
});
