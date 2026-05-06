const { ethers } = require("hardhat");
const { exec } = require("child_process");
const { promisify } = require("util");
const fs = require("fs");
const path = require("path");
require("dotenv").config();

const { LockEventQueue, correlationIdFor } = require("./relayer/queue");
const { WorkerPool } = require("./relayer/worker_pool");
const { createMetrics, startMetricsServer } = require("./relayer/metrics");
const { mkLogger } = require("./relayer/logger");
const { CosmosGrpcClient } = require("./relayer/cosmos_client");

const execAsync = promisify(exec);

// COSMOS_HOME / COSMOS_NODE let us point the embedded `simd` CLI at a
// testnet validator home (for the docker testnet, typically node0) and a
// non-default RPC endpoint, without changing the baked-in invocation shape.
// Both unset = current behaviour (use ~/.simapp and the local --node default).
function simdFlags() {
  const parts = [];
  if (process.env.COSMOS_HOME) parts.push(`--home ${process.env.COSMOS_HOME}`);
  if (process.env.COSMOS_NODE) parts.push(`--node ${process.env.COSMOS_NODE}`);
  return parts.length ? " " + parts.join(" ") : "";
}

// Subset of simdFlags for purely-local operations (keyring ops, etc.) that
// don't accept --node. Passing --node to `simd keys show` is rejected with
// "unknown flag: --node".
function simdHomeOnlyFlag() {
  return process.env.COSMOS_HOME ? ` --home ${process.env.COSMOS_HOME}` : "";
}

class CrossChainRelayer {
  constructor(options) {
    const opts = options || {};
    this.ethereumProvider = new ethers.JsonRpcProvider(
      process.env.ETHEREUM_RPC_URL || "http://localhost:8545"
    );

    this.contractAddress = this.getContractAddress();
    this.contract = new ethers.Contract(
      this.contractAddress,
      this.getContractABI(),
      this.ethereumProvider
    );

    this.lastProcessedBlock = 0;
    this.isRunning = false;

    this.logger = opts.logger || mkLogger({ component: "relayer" });
    this.metrics = opts.metrics || createMetrics();
    this.queue =
      opts.queue ||
      new LockEventQueue(
        process.env.RELAYER_DB_PATH || path.join(__dirname, "..", "data", "relayer.sqlite"),
        { maxAttempts: parseInt(process.env.RELAYER_MAX_ATTEMPTS || "5", 10) }
      );
    this.workerPool =
      opts.workerPool ||
      new WorkerPool(this.queue, (row) => this.processQueuedMint(row), {
        concurrency: parseInt(process.env.RELAYER_CONCURRENCY || "4", 10),
        metrics: this.metrics,
        logger: this.logger.child({ component: "worker_pool" }),
      });
    this.metricsServer = null;

    // Cosmos-side submission mutex. Two compounding issues motivate this:
    //   (1) Concurrent workers submitting from the same key race on the
    //       account sequence — classic sequence-mismatch rejection.
    //   (2) Even at concurrency=1, `simd tx` reads the account sequence
    //       from *committed* chain state, not the mempool. So the next
    //       submit within a ~5s block time sees the old sequence and
    //       rebuilds the tx with a duplicate → rejected. This caps the
    //       single-signer ceiling at 1 tx / block (~0.2 tx/s).
    // Fix for (2): we track account_number + next sequence in process and
    // pass them explicitly to `simd tx` via --account-number / --sequence,
    // bypassing simd's stale-state lookup entirely. The mutex below
    // guards the cache against races when we restore concurrency later.
    this.cosmosSubmitLock = Promise.resolve();
    this.signerAddress = null;
    this.accountNumber = null;
    this.nextSequence = null;
    this.grpcClient = null;

    console.log("🚀 Relayer initialized");
    console.log("📄 Contract Address:", this.contractAddress);
    this.logger.info("relayer_initialized", {
      contract_address: this.contractAddress,
      concurrency: this.workerPool.concurrency,
    });
  }

  getContractAddress() {
    try {
      const deploymentPath = path.join(
        __dirname,
        "..",
        "ignition",
        "deployments",
        "chain-31337",
        "deployed_addresses.json"
      );

      const deployedAddresses = JSON.parse(fs.readFileSync(deploymentPath, "utf8"));
      const address = deployedAddresses["LockAndMintModule#LockAndMint"];

      if (!address) {
        throw new Error("Contract address not found");
      }

      return address;
    } catch (error) {
      console.error("❌ Failed to get contract address:", error);
      throw error;
    }
  }

  getContractABI() {
    try {
      const artifactPath = path.join(
        __dirname,
        "..",
        "artifacts",
        "contracts",
        "LockAndMint.sol",
        "LockAndMint.json"
      );

      const artifact = JSON.parse(fs.readFileSync(artifactPath, "utf8"));
      return artifact.abi;
    } catch (error) {
      console.error("❌ Failed to get contract ABI:", error);
      throw error;
    }
  }

  async start() {
    try {
      console.log("🚀 Starting Cross-Chain Relayer...");

      await this.testCosmosConnection();
      await this.initSignerState();
      this.lastProcessedBlock = await this.ethereumProvider.getBlockNumber();
      console.log("📦 Starting from block:", this.lastProcessedBlock);

      const metricsPort = parseInt(process.env.RELAYER_METRICS_PORT || "9464", 10);
      this.metricsServer = await startMetricsServer(this.metrics, metricsPort);
      this.logger.info("metrics_server_started", { port: metricsPort });

      this.workerPool.start();

      this.isRunning = true;
      this.pollForEvents();

      console.log("✅ Relayer is now running!");
      console.log("👁️  Watching for TokensLocked events...");
      console.log(`📊 Metrics: http://localhost:${metricsPort}/metrics`);
    } catch (error) {
      console.error("❌ Failed to start relayer:", error);
      this.logger.error("relayer_start_failed", { error: error.message });
      throw error;
    }
  }

  async testCosmosConnection() {
    try {
      const command = `cd ${process.env.COSMOS_CLI_PATH.replace('/build/simd', '')} && ./build/simd status${simdFlags()}`;
      const { stdout } = await execAsync(command);
      const status = JSON.parse(stdout);
      console.log("✅ Cosmos connection OK - Latest block:", status.sync_info.latest_block_height);
    } catch (error) {
      console.error("❌ Failed to connect to Cosmos:", error);
      throw new Error("Cosmos CLI connection failed");
    }
  }

  async initSignerState() {
    const cosmosPath = process.env.COSMOS_CLI_PATH.replace('/build/simd', '');
    const keyName = process.env.COSMOS_FROM_KEY;
    if (!keyName) throw new Error("COSMOS_FROM_KEY env var is required");
    const addrCmd = `cd ${cosmosPath} && ./build/simd keys show ${keyName} -a --keyring-backend test${simdHomeOnlyFlag()}`;
    const { stdout: addrOut } = await execAsync(addrCmd);
    this.signerAddress = addrOut.trim();
    await this._resyncSequence();

    // Export the raw privkey hex from the test keyring so we can sign in
    // process via cosmjs DirectSecp256k1Wallet. `simd keys export
    // --unarmored-hex --unsafe` prints a "y/N" warning prompt on stderr;
    // pipe `y\n` into stdin to auto-confirm. The key is emitted on stderr
    // as a 64-hex-char line (not stdout — that's an oddity of simd's keys
    // export implementation), so scan both.
    const expCmd = `cd ${cosmosPath} && echo "y" | ./build/simd keys export ${keyName} --unarmored-hex --unsafe --keyring-backend test${simdHomeOnlyFlag()}`;
    const { stdout: expOut, stderr: expErr } = await execAsync(expCmd);
    const hexMatch = ((expErr || "") + "\n" + (expOut || "")).match(/\b[0-9a-fA-F]{64}\b/);
    if (!hexMatch) {
      throw new Error("failed to extract privkey hex from simd keys export");
    }
    const privKeyHex = hexMatch[0];

    const rpcUrl = (process.env.COSMOS_NODE || "tcp://127.0.0.1:26657").replace(/^tcp:/, "http:");
    const chainId = process.env.COSMOS_CHAIN_ID;
    if (!chainId) throw new Error("COSMOS_CHAIN_ID env var is required");

    this.grpcClient = new CosmosGrpcClient();
    await this.grpcClient.init({
      privKeyHex,
      rpcUrl,
      chainId,
      addressPrefix: process.env.COSMOS_ADDR_PREFIX || "cosmos",
    });
    if (this.grpcClient.signerAddress !== this.signerAddress) {
      throw new Error(
        `signer address mismatch: keys-show=${this.signerAddress} cosmjs=${this.grpcClient.signerAddress}`
      );
    }

    console.log(`🔑 Signer initialised: addr=${this.signerAddress} acct#=${this.accountNumber} seq=${this.nextSequence}`);
    this.logger.info("signer_state_initialised", {
      address: this.signerAddress,
      account_number: this.accountNumber,
      sequence: this.nextSequence,
      rpc_url: rpcUrl,
      transport: "cosmjs-grpc",
    });
  }

  async _resyncSequence() {
    const cosmosPath = process.env.COSMOS_CLI_PATH.replace('/build/simd', '');
    const qCmd = `cd ${cosmosPath} && ./build/simd q auth account ${this.signerAddress} --output json${simdFlags()}`;
    const { stdout } = await execAsync(qCmd);
    const doc = JSON.parse(stdout);
    // cosmos-sdk v0.50+: { account: { @type, address, pub_key, account_number, sequence } }
    // older variants wrap fields under value/base_account — handle both.
    const acct = doc.account || doc;
    const dig = acct.value || acct.base_account || acct;
    const accNum = parseInt(dig.account_number ?? "0", 10);
    const seq = parseInt(dig.sequence ?? "0", 10);
    this.accountNumber = accNum;
    this.nextSequence = seq;
  }

  // Graceful shutdown: stop accepting new events, let in-flight workers finish
  // their current mint, flush metrics, close the queue. The SQLite queue is
  // WAL-journaled so any rows still in 'pending' survive the restart.
  async stop() {
    if (!this.isRunning && !this.workerPool) {
      return;
    }
    this.logger.info("relayer_shutdown_begin", { queue_depth: safeDepth(this.queue) });
    this.isRunning = false;
    if (this.workerPool) {
      await this.workerPool.stop();
    }
    if (this.metricsServer) {
      await new Promise((resolve) => this.metricsServer.close(() => resolve()));
    }
    if (this.queue) {
      this.queue.close();
    }
    if (this.grpcClient) {
      this.grpcClient.disconnect();
      this.grpcClient = null;
    }
    this.logger.info("relayer_shutdown_complete");
    console.log("🛑 Relayer stopped");
  }

  async pollForEvents() {
    while (this.isRunning) {
      try {
        await this.checkForNewEvents();
        await new Promise(resolve =>
          setTimeout(resolve, parseInt(process.env.POLL_INTERVAL || "5000"))
        );
      } catch (error) {
        console.error("❌ Error in event polling:", error);
        this.logger.error("poll_error", { error: error.message });
        await new Promise(resolve => setTimeout(resolve, 10000));
      }
    }
  }

  async checkForNewEvents() {
    const currentBlock = await this.ethereumProvider.getBlockNumber();

    if (currentBlock <= this.lastProcessedBlock) {
      return;
    }

    console.log(`🔍 Checking blocks ${this.lastProcessedBlock + 1} to ${currentBlock}`);

    const filter = this.contract.filters.TokensLocked();
    const events = await this.contract.queryFilter(
      filter,
      this.lastProcessedBlock + 1,
      currentBlock
    );

    for (const event of events) {
      await this.processLockEvent(event);
    }

    this.lastProcessedBlock = currentBlock;
  }

  async processLockEvent(event) {
    try {
      console.log("\n🎯 New TokensLocked event detected!");
      console.log("📦 Block:", event.blockNumber);
      console.log("🔗 Tx Hash:", event.transactionHash);

      const tx = await this.ethereumProvider.getTransaction(event.transactionHash);
      const decodedData = this.contract.interface.parseTransaction({ data: tx.data });

      const cosmosReceiver = decodedData.args[1];
      const logIndex = event.logIndex != null ? event.logIndex : event.index;
      const correlationId = correlationIdFor(event.transactionHash, logIndex);

      let lockMinedAtMs = null;
      try {
        const block = await this.ethereumProvider.getBlock(event.blockNumber);
        if (block && block.timestamp) {
          lockMinedAtMs = Number(block.timestamp) * 1000;
        }
      } catch (_) {
        // Non-fatal: e2e latency just won't be observed for this event.
      }

      const queueEvent = {
        correlation_id: correlationId,
        eth_tx_hash: event.transactionHash,
        eth_block_number: event.blockNumber,
        log_index: logIndex,
        owner: event.args[0],
        cosmos_receiver: cosmosReceiver,
        amount: event.args[2].toString(),
        lock_timestamp: event.args[3].toString(),
        lock_mined_at_ms: lockMinedAtMs,
        observed_at_ms: Date.now(),
      };

      const inserted = this.queue.enqueue(queueEvent);
      this.metrics.eventsObserved.inc({ result: inserted ? "enqueued" : "deduped" });

      console.log("📋 Event Details:");
      console.log("  👤 Owner:", queueEvent.owner);
      console.log("  🌌 Cosmos Receiver:", queueEvent.cosmos_receiver);
      console.log("  💰 Amount:", queueEvent.amount);
      console.log("  🧭 Correlation:", correlationId, "(", inserted ? "new" : "duplicate", ")");

      this.logger.info("event_enqueued", {
        correlation_id: correlationId,
        inserted,
        eth_block: event.blockNumber,
        amount: queueEvent.amount,
      });
    } catch (error) {
      console.error("❌ Error processing lock event:", error);
      this.logger.error("event_observe_error", {
        error: error.message,
        tx_hash: event && event.transactionHash,
      });
    }
  }

  // Handler invoked by the worker pool for one claimed queue row. Delegates to
  // the existing mintOnCosmos logic and maps its success/failure onto the
  // worker-pool contract (return value / thrown error).
  async processQueuedMint(row) {
    const lockEvent = {
      owner: row.owner,
      cosmosReceiver: row.cosmos_receiver,
      amount: row.amount,
      timestamp: row.lock_timestamp,
      blockNumber: row.eth_block_number,
      transactionHash: row.eth_tx_hash,
    };
    const outcome = await this.mintOnCosmos(lockEvent);
    if (outcome && outcome.ok === false) {
      const err = new Error(outcome.error || "mint failed");
      err.code = outcome.code || "mint_failed";
      if (outcome.fatal) err.fatal = true;
      throw err;
    }
    return { cosmosTxHash: outcome && outcome.cosmosTxHash };
  }

  async _runCosmosTxSerialised(submitter) {
    // Chain onto the previous submit's completion (ignoring its failure —
    // a failure must not break the chain for future callers). Inside the
    // critical section we reserve (account_number, sequence) from the
    // cache and hand them to the submitter. On success we pre-increment
    // the cached sequence; on failure we invalidate it so the next submit
    // re-queries the chain.
    const prev = this.cosmosSubmitLock.catch(() => {});
    let releaseLock;
    this.cosmosSubmitLock = new Promise((resolve) => { releaseLock = resolve; });
    try {
      await prev;
      if (this.nextSequence == null || this.accountNumber == null) {
        await this._resyncSequence();
      }
      const seq = this.nextSequence;
      const acct = this.accountNumber;
      try {
        const res = await submitter(seq, acct);
        this.nextSequence = seq + 1;
        return res;
      } catch (e) {
        this.nextSequence = null;
        throw e;
      }
    } finally {
      releaseLock();
    }
  }

  async mintOnCosmos(lockEvent) {
    // Direct-broadcast path via cosmjs. No CLI shell-out. We still hold
    // the cosmosSubmitLock to atomically reserve (seq, acct) for the
    // caller, but the critical section is now just the sign+broadcast
    // round-trip (~10-50 ms) instead of a ~1 s simd process spawn.
    const cosmosFees = process.env.COSMOS_FEES || "5000stake";
    const cosmosGas = process.env.COSMOS_GAS || "300000";
    const feeAmount = cosmosFees.match(/^(\d+)(.*)$/);
    const fee = {
      amount: feeAmount
        ? [{ amount: feeAmount[1], denom: feeAmount[2] || "stake" }]
        : [{ amount: "5000", denom: "stake" }],
      gas: cosmosGas,
    };

    const t0 = Date.now();
    let result;
    try {
      result = await this._runCosmosTxSerialised(async (seq, acct) => {
        return await this.grpcClient.submitMint({
          authority: this.signerAddress,
          receiver: lockEvent.cosmosReceiver,
          amount: lockEvent.amount,
          sequence: seq,
          accountNumber: acct,
          fee,
          memo: "",
        });
      });
    } catch (error) {
      const durMs = Date.now() - t0;
      const code = error.checkTxCode;
      const log = error.log || error.message;
      console.error(`❌ mint failed code=${code} dur=${durMs}ms log=${log}`);
      this.logger.error("cosmos_mint_error", {
        check_tx_code: code,
        log,
        duration_ms: durMs,
        receiver: lockEvent.cosmosReceiver,
      });
      return {
        ok: false,
        code: code != null ? `checktx_${code}` : "exec_failed",
        error: log,
      };
    }

    const durMs = Date.now() - t0;
    this.logger.info("cosmos_mint_ok", {
      tx_hash: result.txHash,
      duration_ms: durMs,
      sequence_used: this.nextSequence - 1,
    });
    return { ok: true, cosmosTxHash: result.txHash };
  }

  async verifyCosmosTransaction(lockEvent) {
    try {
      console.log("🔍 Verifying cosmos transaction by checking balance...");

      const cosmosPath = process.env.COSMOS_CLI_PATH.replace('/build/simd', '');
      const queryCommand = `cd ${cosmosPath} && ./build/simd query lockandmint balance "${lockEvent.cosmosReceiver}" --output json${simdFlags()}`;

      const { stdout } = await execAsync(queryCommand);
      const balanceResult = JSON.parse(stdout);

      console.log("💰 Current cosmos balance:", balanceResult.balance);

      if (parseInt(balanceResult.balance) >= parseInt(lockEvent.amount)) {
        console.log("✅ Verification successful! Transaction appears to have completed.");
        console.log("🎉 Cross-chain relay completed!");
        console.log("  📡 Ethereum → Cosmos");
        console.log("  💰 Amount:", lockEvent.amount);
        console.log("  🎯 Receiver:", lockEvent.cosmosReceiver);
        console.log("  🔗 ETH Tx:", lockEvent.transactionHash);
        return true;
      } else {
        console.log("❌ Verification failed. Balance not updated.");
        return false;
      }

    } catch (verifyError) {
      console.error("❌ Failed to verify transaction:", verifyError);
      return false;
    }
  }
}

function safeDepth(queue) {
  try {
    return queue && queue.depth();
  } catch (_) {
    return null;
  }
}

async function main() {
  const relayer = new CrossChainRelayer();

  const shutdown = async (signal) => {
    console.log(`\n⏹️  Received ${signal}, flushing and shutting down...`);
    try {
      await relayer.stop();
    } catch (e) {
      console.error("shutdown error:", e);
    }
    process.exit(0);
  };
  process.on("SIGINT", () => shutdown("SIGINT"));
  process.on("SIGTERM", () => shutdown("SIGTERM"));

  try {
    await relayer.start();
  } catch (error) {
    console.error("❌ Relayer failed to start:", error);
    process.exit(1);
  }
}

if (require.main === module) {
  main();
}

module.exports = { CrossChainRelayer };
