"use strict";

const fs = require("fs");
const path = require("path");
const Database = require("better-sqlite3");

const SCHEMA = `
CREATE TABLE IF NOT EXISTS lock_events (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  correlation_id    TEXT    NOT NULL UNIQUE,
  eth_tx_hash       TEXT    NOT NULL,
  eth_block_number  INTEGER NOT NULL,
  log_index         INTEGER NOT NULL,
  owner             TEXT    NOT NULL,
  cosmos_receiver   TEXT    NOT NULL,
  amount            TEXT    NOT NULL,
  lock_timestamp    TEXT    NOT NULL,
  lock_mined_at_ms  INTEGER,
  observed_at_ms    INTEGER NOT NULL,
  status            TEXT    NOT NULL
                    CHECK(status IN ('pending','in_progress','done','failed'))
                    DEFAULT 'pending',
  attempts          INTEGER NOT NULL DEFAULT 0,
  cosmos_tx_hash    TEXT,
  last_error        TEXT,
  completed_at_ms   INTEGER,
  UNIQUE(eth_tx_hash, log_index)
);
CREATE INDEX IF NOT EXISTS idx_lock_events_status ON lock_events(status, id);
`;

class LockEventQueue {
  constructor(dbPath, options) {
    const opts = options || {};
    if (dbPath !== ":memory:") {
      fs.mkdirSync(path.dirname(dbPath), { recursive: true });
    }
    this.db = new Database(dbPath);
    this.db.pragma("journal_mode = WAL");
    this.db.pragma("synchronous = NORMAL");
    this.db.pragma("foreign_keys = ON");
    this.db.exec(SCHEMA);
    this.maxAttempts = opts.maxAttempts || 5;

    // Crash recovery: any row stuck in_progress at startup is returned to
    // pending so a worker can pick it up. Audit trail is preserved because
    // rows are never deleted and attempts counter is not reset.
    this.db.prepare(
      "UPDATE lock_events SET status = 'pending' WHERE status = 'in_progress'"
    ).run();

    this._enqueue = this.db.prepare(`
      INSERT INTO lock_events (
        correlation_id, eth_tx_hash, eth_block_number, log_index,
        owner, cosmos_receiver, amount, lock_timestamp,
        lock_mined_at_ms, observed_at_ms
      ) VALUES (
        @correlation_id, @eth_tx_hash, @eth_block_number, @log_index,
        @owner, @cosmos_receiver, @amount, @lock_timestamp,
        @lock_mined_at_ms, @observed_at_ms
      )
      ON CONFLICT(eth_tx_hash, log_index) DO NOTHING
    `);

    this._claim = this.db.prepare(`
      UPDATE lock_events
         SET status = 'in_progress',
             attempts = attempts + 1
       WHERE id = (
         SELECT id FROM lock_events
          WHERE status = 'pending'
          ORDER BY id ASC
          LIMIT 1
       )
       RETURNING *
    `);

    this._complete = this.db.prepare(`
      UPDATE lock_events
         SET status = 'done',
             cosmos_tx_hash = @cosmos_tx_hash,
             completed_at_ms = @completed_at_ms,
             last_error = NULL
       WHERE id = @id
    `);

    this._markFail = this.db.prepare(`
      UPDATE lock_events
         SET status = @status,
             last_error = @last_error,
             completed_at_ms = CASE WHEN @status = 'failed' THEN @now ELSE completed_at_ms END
       WHERE id = @id
    `);

    this._depth = this.db.prepare(
      "SELECT COUNT(*) AS c FROM lock_events WHERE status = 'pending'"
    );

    this._counts = this.db.prepare(
      "SELECT status, COUNT(*) AS c FROM lock_events GROUP BY status"
    );

    this._getByCorrelation = this.db.prepare(
      "SELECT * FROM lock_events WHERE correlation_id = ?"
    );

    this._all = this.db.prepare("SELECT * FROM lock_events ORDER BY id ASC");
  }

  // Enqueue is idempotent on (eth_tx_hash, log_index). Returns true if a new
  // row was inserted, false if it already existed.
  enqueue(ev) {
    const info = this._enqueue.run({
      correlation_id: ev.correlation_id,
      eth_tx_hash: ev.eth_tx_hash,
      eth_block_number: ev.eth_block_number,
      log_index: ev.log_index,
      owner: ev.owner,
      cosmos_receiver: ev.cosmos_receiver,
      amount: String(ev.amount),
      lock_timestamp: String(ev.lock_timestamp),
      lock_mined_at_ms: ev.lock_mined_at_ms || null,
      observed_at_ms: ev.observed_at_ms || Date.now(),
    });
    return info.changes === 1;
  }

  // Atomically transitions a single pending row to in_progress and returns it.
  // Returns null if the queue has no pending rows.
  claim() {
    return this._claim.get() || null;
  }

  complete(id, cosmosTxHash) {
    this._complete.run({
      id,
      cosmos_tx_hash: cosmosTxHash || null,
      completed_at_ms: Date.now(),
    });
  }

  // Mark a row as failed. If retryable and attempts < maxAttempts, return it
  // to pending; otherwise terminally fail it.
  fail(id, error, opts) {
    const row = this.db.prepare("SELECT attempts FROM lock_events WHERE id = ?").get(id);
    const retryable = !(opts && opts.fatal);
    const status =
      retryable && row && row.attempts < this.maxAttempts ? "pending" : "failed";
    this._markFail.run({
      id,
      status,
      last_error: String(error && error.message ? error.message : error).slice(0, 4000),
      now: Date.now(),
    });
    return status;
  }

  depth() {
    return this._depth.get().c;
  }

  countsByStatus() {
    const out = { pending: 0, in_progress: 0, done: 0, failed: 0 };
    for (const row of this._counts.all()) out[row.status] = row.c;
    return out;
  }

  getByCorrelation(correlationId) {
    return this._getByCorrelation.get(correlationId) || null;
  }

  all() {
    return this._all.all();
  }

  close() {
    this.db.close();
  }
}

function correlationIdFor(txHash, logIndex) {
  return `${txHash}:${logIndex}`;
}

module.exports = { LockEventQueue, correlationIdFor };
