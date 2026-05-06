"use strict";

const { expect } = require("chai");
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");

const { LockEventQueue, correlationIdFor } = require("../scripts/relayer/queue");
const { WorkerPool } = require("../scripts/relayer/worker_pool");
const { createMetrics, startMetricsServer } = require("../scripts/relayer/metrics");

function mkTmpDb() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "relayer-test-"));
  return { dir, dbPath: path.join(dir, "relayer.sqlite") };
}

function mkEvent(i, extra) {
  const txHash = "0x" + String(i).padStart(64, "0");
  const logIndex = (extra && extra.log_index != null) ? extra.log_index : 0;
  return Object.assign(
    {
      correlation_id: correlationIdFor(txHash, logIndex),
      eth_tx_hash: txHash,
      eth_block_number: 1000 + i,
      log_index: logIndex,
      owner: "0xowner",
      cosmos_receiver: "cosmos1receiver",
      amount: String(1000 + i),
      lock_timestamp: String(1700000000 + i),
      lock_mined_at_ms: 1700000000000 + i * 1000,
      observed_at_ms: Date.now(),
    },
    extra || {}
  );
}

describe("LockEventQueue", function () {
  describe("durability across restart", function () {
    it("persists pending rows across process restart", function () {
      const { dir, dbPath } = mkTmpDb();
      try {
        const q1 = new LockEventQueue(dbPath);
        expect(q1.enqueue(mkEvent(1))).to.equal(true);
        expect(q1.enqueue(mkEvent(2))).to.equal(true);
        expect(q1.enqueue(mkEvent(3))).to.equal(true);
        expect(q1.depth()).to.equal(3);
        q1.close();

        const q2 = new LockEventQueue(dbPath);
        expect(q2.depth()).to.equal(3);
        const claimed = q2.claim();
        expect(claimed).to.not.equal(null);
        expect(claimed.correlation_id).to.equal(correlationIdFor("0x" + "1".padStart(64, "0"), 0));
        q2.complete(claimed.id, "COSMOS_TX_AAA");
        q2.close();

        const q3 = new LockEventQueue(dbPath);
        const counts = q3.countsByStatus();
        expect(counts.done).to.equal(1);
        expect(counts.pending).to.equal(2);
        expect(counts.in_progress).to.equal(0);
        q3.close();
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });

    it("recovers in_progress rows back to pending after a crash", function () {
      const { dir, dbPath } = mkTmpDb();
      try {
        const q1 = new LockEventQueue(dbPath);
        q1.enqueue(mkEvent(1));
        q1.enqueue(mkEvent(2));
        const claimed = q1.claim();
        expect(claimed.status).to.equal("in_progress");
        expect(q1.countsByStatus().in_progress).to.equal(1);
        // Simulate crash: close without completing.
        q1.close();

        const q2 = new LockEventQueue(dbPath);
        const counts = q2.countsByStatus();
        expect(counts.in_progress).to.equal(0);
        expect(counts.pending).to.equal(2);
        // Audit trail: attempts counter preserved from the crashed attempt.
        const row = q2.getByCorrelation(claimed.correlation_id);
        expect(row.attempts).to.equal(1);
        q2.close();
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });

    it("enqueue is idempotent on (eth_tx_hash, log_index)", function () {
      const { dir, dbPath } = mkTmpDb();
      try {
        const q = new LockEventQueue(dbPath);
        expect(q.enqueue(mkEvent(1))).to.equal(true);
        expect(q.enqueue(mkEvent(1))).to.equal(false); // duplicate
        expect(q.depth()).to.equal(1);
        // Different log_index on same tx is a different event.
        expect(q.enqueue(mkEvent(1, { log_index: 1, correlation_id: correlationIdFor("0x" + "1".padStart(64, "0"), 1) }))).to.equal(true);
        expect(q.depth()).to.equal(2);
        q.close();
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });

    it("requeues retryable failures below maxAttempts and terminally fails once exhausted", function () {
      const { dir, dbPath } = mkTmpDb();
      try {
        const q = new LockEventQueue(dbPath, { maxAttempts: 2 });
        q.enqueue(mkEvent(1));
        let row = q.claim();
        expect(q.fail(row.id, new Error("boom"))).to.equal("pending");
        row = q.claim();
        expect(row.attempts).to.equal(2);
        expect(q.fail(row.id, new Error("boom again"))).to.equal("failed");
        expect(q.countsByStatus().failed).to.equal(1);
        expect(q.countsByStatus().pending).to.equal(0);
        q.close();
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });
  });

  describe("concurrent worker correctness", function () {
    it("processes every event exactly once under 8 concurrent workers", async function () {
      this.timeout(10000);
      const { dir, dbPath } = mkTmpDb();
      try {
        const q = new LockEventQueue(dbPath);
        const N = 100;
        for (let i = 1; i <= N; i++) {
          expect(q.enqueue(mkEvent(i))).to.equal(true);
        }

        const seen = new Map();
        const metrics = createMetrics();
        const pool = new WorkerPool(
          q,
          async (row) => {
            // Randomised tiny delay to force interleaving between workers.
            await new Promise((r) => setTimeout(r, Math.floor(Math.random() * 5)));
            const prev = seen.get(row.correlation_id) || 0;
            seen.set(row.correlation_id, prev + 1);
            return { cosmosTxHash: "COSMOS_" + row.id };
          },
          { concurrency: 8, idleSleepMs: 5, metrics }
        );
        pool.start();

        // Wait until the queue is fully drained.
        const deadline = Date.now() + 5000;
        while (q.depth() > 0 && Date.now() < deadline) {
          await new Promise((r) => setTimeout(r, 10));
        }
        // Allow in-flight workers to finish committing.
        await new Promise((r) => setTimeout(r, 100));
        await pool.stop();

        expect(seen.size).to.equal(N);
        for (const [, count] of seen) {
          expect(count).to.equal(1); // no double-mint
        }
        const counts = q.countsByStatus();
        expect(counts.done).to.equal(N);
        expect(counts.pending).to.equal(0);
        expect(counts.in_progress).to.equal(0);
        q.close();
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });

    it("requeues transient failures and eventually drains", async function () {
      this.timeout(10000);
      const { dir, dbPath } = mkTmpDb();
      try {
        const q = new LockEventQueue(dbPath, { maxAttempts: 5 });
        const N = 20;
        for (let i = 1; i <= N; i++) q.enqueue(mkEvent(i));

        const failureCount = new Map();
        const pool = new WorkerPool(
          q,
          async (row) => {
            const prev = failureCount.get(row.correlation_id) || 0;
            if (prev < 1) {
              failureCount.set(row.correlation_id, prev + 1);
              throw new Error("flaky once");
            }
            return { cosmosTxHash: "OK_" + row.id };
          },
          { concurrency: 4, idleSleepMs: 5 }
        );
        pool.start();

        const deadline = Date.now() + 5000;
        while (q.countsByStatus().done < N && Date.now() < deadline) {
          await new Promise((r) => setTimeout(r, 10));
        }
        await pool.stop();

        const counts = q.countsByStatus();
        expect(counts.done).to.equal(N);
        expect(counts.failed).to.equal(0);
        q.close();
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });
  });

  describe("metrics exposure", function () {
    let server, port, metrics;

    beforeEach(async function () {
      metrics = createMetrics();
      server = await startMetricsServer(metrics, 0, "127.0.0.1");
      port = server.address().port;
    });

    afterEach(async function () {
      await new Promise((r) => server.close(() => r()));
    });

    function fetchMetrics() {
      return new Promise((resolve, reject) => {
        http.get({ host: "127.0.0.1", port, path: "/metrics" }, (res) => {
          let body = "";
          res.on("data", (c) => (body += c));
          res.on("end", () => resolve({ status: res.statusCode, body }));
        }).on("error", reject);
      });
    }

    it("exposes all required metric names", async function () {
      metrics.eventsObserved.inc({ result: "enqueued" });
      metrics.eventsProcessed.inc();
      metrics.eventsFailed.inc({ reason: "exec_failed" });
      metrics.queueDepth.set(3);
      metrics.e2eLatency.observe(12.5);
      metrics.mintDuration.observe(0.42);

      const res = await fetchMetrics();
      expect(res.status).to.equal(200);
      const required = [
        "relayer_events_observed_total",
        "relayer_events_processed_total",
        "relayer_events_failed_total",
        "relayer_queue_depth",
        "relayer_e2e_latency_seconds",
        "relayer_mint_submit_duration_seconds",
      ];
      for (const name of required) {
        expect(res.body, `metric ${name} missing`).to.match(new RegExp(`^# HELP ${name} `, "m"));
      }
      // Label presence checks.
      expect(res.body).to.match(/relayer_events_observed_total\{result="enqueued"\} 1/);
      expect(res.body).to.match(/relayer_events_failed_total\{reason="exec_failed"\} 1/);
      expect(res.body).to.match(/relayer_queue_depth 3/);
    });

    it("/healthz returns 200 ok", async function () {
      const out = await new Promise((resolve, reject) => {
        http.get({ host: "127.0.0.1", port, path: "/healthz" }, (res) => {
          let body = "";
          res.on("data", (c) => (body += c));
          res.on("end", () => resolve({ status: res.statusCode, body }));
        }).on("error", reject);
      });
      expect(out.status).to.equal(200);
      expect(JSON.parse(out.body).status).to.equal("ok");
    });
  });
});
