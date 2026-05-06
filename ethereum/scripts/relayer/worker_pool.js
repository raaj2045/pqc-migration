"use strict";

const { mkLogger } = require("./logger");

// WorkerPool drains a LockEventQueue with `concurrency` cooperating async
// workers. Each worker loops: claim() -> handler(row) -> complete() / fail().
// Graceful stop lets in-flight workers finish their current row, then resolves.
class WorkerPool {
  constructor(queue, handler, options) {
    const opts = options || {};
    this.queue = queue;
    this.handler = handler;
    this.concurrency = opts.concurrency || 4;
    this.idleSleepMs = opts.idleSleepMs || 250;
    this.metrics = opts.metrics || null;
    this.logger = opts.logger || mkLogger({ component: "worker_pool" });
    this.stopping = false;
    this.workers = [];
    this.depthTimer = null;
    this.depthIntervalMs = opts.depthIntervalMs || 1000;
  }

  start() {
    for (let i = 0; i < this.concurrency; i++) {
      this.workers.push(this._workerLoop(i));
    }
    if (this.metrics) {
      this._publishDepth();
      this.depthTimer = setInterval(() => this._publishDepth(), this.depthIntervalMs);
      if (this.depthTimer.unref) this.depthTimer.unref();
    }
    this.logger.info("worker_pool_started", { concurrency: this.concurrency });
  }

  async stop() {
    if (this.stopping) return;
    this.stopping = true;
    if (this.depthTimer) clearInterval(this.depthTimer);
    await Promise.all(this.workers);
    this.logger.info("worker_pool_stopped");
  }

  _publishDepth() {
    if (!this.metrics) return;
    try {
      this.metrics.queueDepth.set(this.queue.depth());
    } catch (err) {
      this.logger.warn("queue_depth_probe_failed", { error: String(err) });
    }
  }

  _sleep(ms) {
    return new Promise((resolve) => {
      const t = setTimeout(resolve, ms);
      if (t.unref) t.unref();
    });
  }

  async _workerLoop(workerIdx) {
    const log = this.logger.child({ worker: workerIdx });
    while (!this.stopping) {
      let row;
      try {
        row = this.queue.claim();
      } catch (err) {
        log.error("claim_error", { error: String(err) });
        await this._sleep(this.idleSleepMs);
        continue;
      }
      if (!row) {
        await this._sleep(this.idleSleepMs);
        continue;
      }
      const evLog = log.child({ correlation_id: row.correlation_id, id: row.id, attempt: row.attempts });
      evLog.info("event_claimed");
      const started = Date.now();
      try {
        const result = await this.handler(row);
        this.queue.complete(row.id, result && result.cosmosTxHash);
        if (this.metrics) {
          this.metrics.eventsProcessed.inc();
          this.metrics.mintDuration.observe((Date.now() - started) / 1000);
          if (row.lock_mined_at_ms) {
            this.metrics.e2eLatency.observe((Date.now() - row.lock_mined_at_ms) / 1000);
          }
        }
        evLog.info("event_processed", {
          cosmos_tx_hash: result && result.cosmosTxHash,
          duration_ms: Date.now() - started,
        });
      } catch (err) {
        const reason = (err && err.code) || "error";
        const status = this.queue.fail(row.id, err, { fatal: !!(err && err.fatal) });
        if (this.metrics) {
          this.metrics.eventsFailed.inc({ reason: String(reason) });
          this.metrics.mintDuration.observe((Date.now() - started) / 1000);
        }
        evLog.warn("event_failed", {
          reason: String(reason),
          status_next: status,
          error: err && err.message ? err.message : String(err),
        });
      }
    }
  }
}

module.exports = { WorkerPool };
