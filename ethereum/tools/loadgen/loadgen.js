#!/usr/bin/env node
// Load generator for LockAndMint: paces lock() submissions at a target rate
// across a pool of pre-funded accounts and streams per-tx timing records to
// a JSONL file. Designed to be boring: one file, no external deps beyond
// ethers and what the repo already uses.
"use strict";

const { ethers } = require("ethers");
const fs = require("fs");
const path = require("path");
const { parseArgs } = require("util");

// Hardhat's well-known account[0] private key. Public, documented by Hardhat,
// safe only for a local dev node. Override with --owner-pk for any other setup.
const HARDHAT_ACCOUNT_0_PK =
  "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80";

const DEFAULT_GAS_FUND_ETH = "1.0";
const DEFAULT_LOCK_AMOUNT = 1n;
const DEFAULT_PER_WALLET_TOKENS = 10n ** 12n;

function usage() {
  process.stderr.write(
    [
      "Usage: node loadgen.js --rate <tx/s> --duration <s> --accounts <N> --rpc <url>",
      "",
      "Options:",
      "  --rate N              target submission rate (tx/sec) [required]",
      "  --duration S          how long to run, in seconds [required]",
      "  --accounts N          number of sender wallets to pre-fund [default 10]",
      "  --rpc URL             Ethereum RPC [default http://localhost:8545]",
      "  --contract ADDR       LockAndMint address (auto-detected from ignition deployment if omitted)",
      "  --owner-pk HEX        contract-owner private key [default Hardhat account[0]]",
      "  --amount N            lock amount per tx [default 1]",
      "  --per-wallet-tokens N initial mint per wallet [default 10^12]",
      "  --gas-fund-eth E      ETH to send to each wallet for gas [default 1.0]",
      "  --seed N              deterministic wallet seed [default 1]",
      "  --output PATH         output .jsonl [default results/loadgen_<ts>.jsonl]",
      "",
    ].join("\n")
  );
}

function parseCli() {
  const { values } = parseArgs({
    options: {
      rate: { type: "string" },
      duration: { type: "string" },
      accounts: { type: "string", default: "10" },
      rpc: { type: "string", default: "http://localhost:8545" },
      contract: { type: "string" },
      "owner-pk": { type: "string" },
      amount: { type: "string" },
      "per-wallet-tokens": { type: "string" },
      "gas-fund-eth": { type: "string", default: DEFAULT_GAS_FUND_ETH },
      seed: { type: "string", default: "1" },
      output: { type: "string" },
      help: { type: "boolean", short: "h" },
    },
    allowPositionals: false,
  });
  if (values.help) {
    usage();
    process.exit(0);
  }
  if (!values.rate || !values.duration) {
    usage();
    process.exit(2);
  }
  return {
    rate: Number(values.rate),
    duration: Number(values.duration),
    accounts: Number(values.accounts),
    rpc: values.rpc,
    contract: values.contract,
    ownerPk: values["owner-pk"] || HARDHAT_ACCOUNT_0_PK,
    amount: values.amount ? BigInt(values.amount) : DEFAULT_LOCK_AMOUNT,
    perWalletTokens: values["per-wallet-tokens"]
      ? BigInt(values["per-wallet-tokens"])
      : DEFAULT_PER_WALLET_TOKENS,
    gasFundEth: values["gas-fund-eth"],
    seed: BigInt(values.seed),
    output: values.output,
  };
}

function loadContractArtifact(cli) {
  const artifactPath = path.join(
    __dirname,
    "..",
    "..",
    "artifacts",
    "contracts",
    "LockAndMint.sol",
    "LockAndMint.json"
  );
  const artifact = JSON.parse(fs.readFileSync(artifactPath, "utf8"));
  let address = cli.contract;
  if (!address) {
    const deploymentPath = path.join(
      __dirname,
      "..",
      "..",
      "ignition",
      "deployments",
      "chain-31337",
      "deployed_addresses.json"
    );
    const deployed = JSON.parse(fs.readFileSync(deploymentPath, "utf8"));
    address = deployed["LockAndMintModule#LockAndMint"];
  }
  if (!address) throw new Error("contract address not found; pass --contract");
  return { address, abi: artifact.abi };
}

function deriveWallet(seed, i, provider) {
  // Deterministic per-seed wallet generation. We salt the seed with index and
  // hash to get a 32-byte private key. Good enough for local load testing; do
  // not reuse these keys outside the local dev node.
  const packed = ethers.solidityPacked(["uint256", "uint256"], [seed, i]);
  const pk = ethers.keccak256(packed);
  return new ethers.Wallet(pk, provider);
}

class TokenBucket {
  constructor(rate, capacity) {
    this.rate = rate;
    this.capacity = capacity || Math.max(1, rate);
    this.tokens = this.capacity;
    this.last = Date.now();
  }
  _refill() {
    const now = Date.now();
    const elapsed = (now - this.last) / 1000;
    if (elapsed > 0) {
      this.tokens = Math.min(this.capacity, this.tokens + elapsed * this.rate);
      this.last = now;
    }
  }
  async acquire() {
    while (true) {
      this._refill();
      if (this.tokens >= 1) {
        this.tokens -= 1;
        return;
      }
      const needed = 1 - this.tokens;
      const waitMs = Math.max(1, Math.ceil((needed / this.rate) * 1000));
      await new Promise((r) => setTimeout(r, waitMs));
    }
  }
}

async function preFund(owner, contract, wallets, cli) {
  const log = (msg) => process.stderr.write(msg + "\n");
  log(`[loadgen] pre-funding ${wallets.length} accounts from ${owner.address}`);
  const startNonce = await owner.provider.getTransactionCount(owner.address, "pending");
  let nonce = startNonce;
  const gasValue = ethers.parseEther(String(cli.gasFundEth));

  // Send ETH for gas. Sequential await: Hardhat in automine mode rejects out-
  // of-order nonces (no mempool queue), and this is one-time setup so the
  // serialization cost is trivial.
  for (const w of wallets) {
    const tx = await owner.sendTransaction({ to: w.address, value: gasValue, nonce: nonce++ });
    await tx.wait();
  }
  log(`[loadgen] funded gas (${cli.gasFundEth} ETH each)`);

  // Mint tokens. mint() is onlyOwner.
  for (const w of wallets) {
    const tx = await contract.connect(owner).mint(w.address, cli.perWalletTokens, { nonce: nonce++ });
    await tx.wait();
  }
  log(`[loadgen] minted ${cli.perWalletTokens} tokens each`);
}

async function runLoad(ctx) {
  const { contract, wallets, cli, out, meta } = ctx;
  const rate = cli.rate;
  const bucket = new TokenBucket(rate);
  const endAt = Date.now() + cli.duration * 1000;

  // Per-wallet state: starting nonce + a submission chain used as a mutex so
  // only one send is in flight per signer. Hardhat in automine mode rejects
  // nonce gaps, and JSON-RPC arrival order from parallel sends is not
  // guaranteed, so we serialize nonce-assignment + send per wallet. Different
  // wallets still run in parallel.
  for (const w of wallets) {
    w._nonce = await w.provider.getTransactionCount(w.address, "pending");
    w._submitChain = Promise.resolve();
  }

  const inFlight = new Set();
  let seq = 0;
  let submittedOk = 0;
  let submitErrors = 0;

  const lockIface = contract.interface;
  const lockTopic = lockIface.getEvent("TokensLocked").topicHash;

  const submit = () => {
    const localSeq = seq++;
    const wallet = wallets[localSeq % wallets.length];
    const cosmosReceiver = `cosmos1test${localSeq.toString().padStart(10, "0")}`;
    const amountStr = cli.amount.toString();

    // Chain the submission onto this wallet's queue. The token-bucket paces
    // the call to submit(); per-wallet chaining paces the actual send.
    const chained = wallet._submitChain.then(async () => {
      const nonce = wallet._nonce++;
      const submitTime = Date.now();
      let tx;
      try {
        tx = await contract.connect(wallet).lock(cli.amount, cosmosReceiver, { nonce });
      } catch (err) {
        submitErrors++;
        out.write(JSON.stringify({
          seq: localSeq,
          account_index: localSeq % wallets.length,
          account_address: wallet.address,
          submit_time_ms: submitTime,
          mined_time_ms: null,
          latency_ms: null,
          block_number: null,
          tx_hash: null,
          lock_event_id: null,
          amount: amountStr,
          cosmos_receiver: cosmosReceiver,
          gas_used: null,
          status: null,
          error: String(err && err.message ? err.message : err).slice(0, 500),
        }) + "\n");
        return;
      }
      submittedOk++;
      // Return the tx so the outer await can wait for mining without holding
      // the wallet mutex — a new submission from this wallet can proceed as
      // soon as the send resolved.
      return { tx, submitTime, localSeq, wallet };
    });
    // Release the wallet mutex after the send step regardless of outcome.
    wallet._submitChain = chained.catch(() => {});

    // Separately await mining and write the record.
    const full = chained.then(async (sent) => {
      if (!sent) return;
      const { tx, submitTime, localSeq: s, wallet: w } = sent;
      try {
        const receipt = await tx.wait();
        const minedTime = Date.now();
        const lockLogIdx = receipt.logs.findIndex((l) => l.topics[0] === lockTopic);
        out.write(JSON.stringify({
          seq: s,
          account_index: s % wallets.length,
          account_address: w.address,
          submit_time_ms: submitTime,
          mined_time_ms: minedTime,
          latency_ms: minedTime - submitTime,
          block_number: receipt.blockNumber,
          tx_hash: receipt.hash,
          lock_event_id:
            lockLogIdx >= 0 ? `${receipt.hash}:${receipt.logs[lockLogIdx].index}` : null,
          amount: amountStr,
          cosmos_receiver: cosmosReceiver,
          gas_used: receipt.gasUsed.toString(),
          status: receipt.status,
          error: null,
        }) + "\n");
      } catch (err) {
        submitErrors++;
        out.write(JSON.stringify({
          seq: s,
          account_index: s % wallets.length,
          account_address: w.address,
          submit_time_ms: submitTime,
          mined_time_ms: null,
          latency_ms: null,
          block_number: null,
          tx_hash: tx.hash,
          lock_event_id: null,
          amount: amountStr,
          cosmos_receiver: cosmosReceiver,
          gas_used: null,
          status: null,
          error: String(err && err.message ? err.message : err).slice(0, 500),
        }) + "\n");
      }
    });
    return full;
  };

  process.stderr.write(
    `[loadgen] running: target=${rate} tx/s for ${cli.duration}s across ${wallets.length} wallets\n`
  );

  const progressTimer = setInterval(() => {
    const elapsed = Math.max(0.001, (Date.now() - meta.started_at_ms) / 1000);
    process.stderr.write(
      `  t+${elapsed.toFixed(1)}s  submitted=${seq}  in_flight=${inFlight.size}  ok=${submittedOk}  errs=${submitErrors}\n`
    );
  }, 2000);
  if (progressTimer.unref) progressTimer.unref();

  while (Date.now() < endAt) {
    await bucket.acquire();
    if (Date.now() >= endAt) break;
    const p = submit();
    inFlight.add(p);
    p.finally(() => inFlight.delete(p));
  }

  process.stderr.write(`[loadgen] submission window closed, draining ${inFlight.size} in-flight...\n`);
  await Promise.allSettled(Array.from(inFlight));
  clearInterval(progressTimer);
  return { seq, submittedOk, submitErrors };
}

function mkOutputPath(cli) {
  if (cli.output) return cli.output;
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  return path.join(__dirname, "..", "..", "results", `loadgen_${ts}.jsonl`);
}

async function main() {
  const cli = parseCli();
  const provider = new ethers.JsonRpcProvider(cli.rpc);
  const network = await provider.getNetwork();

  const { address, abi } = loadContractArtifact(cli);
  const owner = new ethers.Wallet(cli.ownerPk, provider);
  const contract = new ethers.Contract(address, abi, provider);

  const wallets = [];
  for (let i = 0; i < cli.accounts; i++) {
    wallets.push(deriveWallet(cli.seed, i, provider));
  }

  const outPath = mkOutputPath(cli);
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  const out = fs.createWriteStream(outPath, { flags: "a" });

  const meta = {
    type: "meta",
    schema: "loadgen/v1",
    rpc: cli.rpc,
    chain_id: Number(network.chainId),
    contract: address,
    target_rate_tx_s: cli.rate,
    duration_s: cli.duration,
    accounts: cli.accounts,
    amount_per_tx: cli.amount.toString(),
    per_wallet_tokens: cli.perWalletTokens.toString(),
    gas_fund_eth: cli.gasFundEth,
    seed: cli.seed.toString(),
    started_at_ms: Date.now(),
  };
  out.write(JSON.stringify(meta) + "\n");

  await preFund(owner, contract, wallets, cli);

  const started = Date.now();
  const totals = await runLoad({ contract, wallets, cli, out, meta });
  const ended = Date.now();

  const trailer = {
    type: "end",
    started_at_ms: started,
    ended_at_ms: ended,
    wall_seconds: (ended - started) / 1000,
    submitted: totals.seq,
    submit_ok: totals.submittedOk,
    submit_errors: totals.submitErrors,
  };
  out.write(JSON.stringify(trailer) + "\n");
  out.end();

  process.stderr.write(
    `[loadgen] done: submitted=${totals.seq} ok=${totals.submittedOk} errors=${totals.submitErrors} -> ${outPath}\n`
  );
}

if (require.main === module) {
  main().catch((e) => {
    process.stderr.write(`[loadgen] fatal: ${e && e.stack ? e.stack : e}\n`);
    process.exit(1);
  });
}

module.exports = { TokenBucket, deriveWallet };
