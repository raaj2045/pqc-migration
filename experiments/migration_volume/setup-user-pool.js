// Create and prepare a pool of INDEPENDENT EVM user accounts for the
// Ethereum -> Cosmos migration direction.
//
// Models many independent users migrating simultaneously, NOT a service
// batching on their behalf: every account is its own wallet, holds its own
// TestERC20, and later submits its own sendTransfer. Deliberately does not
// use ICS20Transfer.multicall — that would model one batching service.
//
// Each account needs three things before it can migrate:
//   1. ETH for gas          (funded from DEPLOYER_PK)
//   2. TestERC20 balance    (TestERC20.mint is unrestricted — self-minted)
//   3. an allowance to ICS20Transfer (sendTransfer pulls into escrow)
//
// Idempotent: reuses accounts already in the pool file, and only does the
// work each account is actually missing.
//
// Written to $DEVNET_DIR/evm-user-pool.json — deliberately a DIFFERENT file
// from batch_scaling's evm-relay-pool.json, so the two experiments' pools
// never share accounts or nonce sequences.
//
// Usage: node setup-user-pool.js <pool-size> [<eth-each>] [<token-each>]
const fs = require("fs");
const path = require("path");
const { loadEnv, ethers, config, abi } = require("../../devnet/lib/lib");

const POOL_FILE = "evm-user-pool.json";
// --pool-file=NAME keeps a second, independent set of accounts in its own file,
// so a run measuring deliveries cannot hand out the same EVM account another
// run is already sending from (two in-flight txs from one account race for the
// same nonce).
const poolFileArg = (process.argv.find((a) => a.startsWith("--pool-file=")) || "").split("=")[1];

(async () => {
  const poolSize = parseInt(process.argv[2], 10);
  if (!Number.isFinite(poolSize) || poolSize < 1) {
    throw new Error("usage: node setup-user-pool.js <size> [eth-each] [token-each] [--pool-file=NAME]");
  }
  const ethEach = process.argv[3] || "1";
  const tokenEach = BigInt(process.argv[4] || "1000000");
  if (!poolSize || poolSize < 1) {
    console.error("usage: node setup-user-pool.js <pool-size> [<eth-each>] [<token-each>]");
    process.exit(2);
  }
  const env = loadEnv();
  config.require_(env, "GETH_RPC", "DEPLOYER_PK", "TEST_ERC20", "ICS20_TRANSFER");
  const url = env.GETH_RPC.startsWith("http") ? env.GETH_RPC : `http://${env.GETH_RPC}`;
  const provider = new ethers.JsonRpcProvider(url, undefined, { cacheTimeout: -1 });
  const deployer = new ethers.Wallet(env.DEPLOYER_PK, provider);
  const ethWei = ethers.parseEther(ethEach);

  const poolFile = path.join(env.DEVNET_DIR, poolFileArg || POOL_FILE);
  let pool = fs.existsSync(poolFile) ? JSON.parse(fs.readFileSync(poolFile, "utf8")) : [];

  for (let i = 0; i < poolSize; i++) {
    if (!pool[i]) {
      const w = ethers.Wallet.createRandom();
      pool[i] = { index: i, address: w.address, privateKey: w.privateKey };
      console.log(`created ${w.address} (index ${i})`);
    }
  }
  const entries = pool.slice(0, poolSize);

  // --- 1. ETH funding, all from the deployer under pre-assigned nonces ----
  const balances = await Promise.all(entries.map((e) => provider.getBalance(e.address)));
  let nonce = await provider.getTransactionCount(deployer.address, "pending");
  const fundTxs = [];
  for (let i = 0; i < entries.length; i++) {
    if (balances[i] < ethWei) {
      fundTxs.push(deployer.sendTransaction({
        to: entries[i].address, value: ethWei - balances[i], nonce: nonce++,
      }));
    }
  }
  if (fundTxs.length) {
    const sent = await Promise.all(fundTxs);
    await Promise.all(sent.map((t) => t.wait()));
    console.log(`funded ${fundTxs.length} account(s) with up to ${ethEach} ETH each`);
  } else {
    console.log("all accounts already hold enough ETH");
  }

  // --- 2 + 3. mint + approve, per account, concurrently across accounts ---
  // Within one account these are strictly ordered (nonce 0 then 1); across
  // accounts they are fully independent, which is the whole point.
  // devnet/abi/TestERC20.json is a trimmed subset and has no allowance();
  // the deployed contract is a full OpenZeppelin ERC20, so add the fragment
  // here rather than mutating the shared devnet ABI.
  const erc20Abi = abi("TestERC20").concat([{
    type: "function", name: "allowance", stateMutability: "view",
    inputs: [{ name: "owner", type: "address" }, { name: "spender", type: "address" }],
    outputs: [{ name: "", type: "uint256" }],
  }]);
  const results = await Promise.all(entries.map(async (e) => {
    const w = new ethers.Wallet(e.privateKey, provider);
    const token = new ethers.Contract(env.TEST_ERC20, erc20Abi, w);
    let n = await provider.getTransactionCount(w.address, "pending");
    const did = [];
    const bal = await token.balanceOf(w.address);
    if (bal < tokenEach) {
      const tx = await token.mint(w.address, tokenEach - bal, { nonce: n++ });
      await tx.wait();
      did.push("mint");
    }
    const allowance = await token.allowance(w.address, env.ICS20_TRANSFER);
    if (allowance < tokenEach) {
      // Approve a large allowance once, so the later submission phase is a
      // single sendTransfer per user and measures only that.
      const tx = await token.approve(env.ICS20_TRANSFER, ethers.MaxUint256, { nonce: n++ });
      await tx.wait();
      did.push("approve");
    }
    return { address: e.address, did, balance: (await token.balanceOf(w.address)).toString() };
  }));

  for (const r of results) {
    console.log(`  ${r.address}: balance ${r.balance}${r.did.length ? ` (did ${r.did.join("+")})` : " (ready)"}`);
  }
  fs.writeFileSync(poolFile, JSON.stringify(pool, null, 2));
  console.log(`wrote ${pool.length} entries to ${poolFile}`);
})().catch((e) => { console.error(e.message || e); process.exit(1); });
