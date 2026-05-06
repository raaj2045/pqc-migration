"use strict";

const http = require("http");
const client = require("prom-client");

function createMetrics() {
  const registry = new client.Registry();
  client.collectDefaultMetrics({ register: registry, prefix: "relayer_" });

  const eventsObserved = new client.Counter({
    name: "relayer_events_observed_total",
    help: "TokensLocked events observed on Ethereum and enqueued (dedup-aware).",
    registers: [registry],
    labelNames: ["result"],
  });

  const eventsProcessed = new client.Counter({
    name: "relayer_events_processed_total",
    help: "Lock events successfully relayed to Cosmos.",
    registers: [registry],
  });

  const eventsFailed = new client.Counter({
    name: "relayer_events_failed_total",
    help: "Lock event processing failures.",
    registers: [registry],
    labelNames: ["reason"],
  });

  const queueDepth = new client.Gauge({
    name: "relayer_queue_depth",
    help: "Number of lock events pending relay.",
    registers: [registry],
  });

  const e2eLatency = new client.Histogram({
    name: "relayer_e2e_latency_seconds",
    help: "End-to-end latency from Ethereum lock mined timestamp to Cosmos mint confirmation.",
    registers: [registry],
    buckets: [1, 2, 5, 10, 20, 30, 60, 120, 300, 600],
  });

  const mintDuration = new client.Histogram({
    name: "relayer_mint_submit_duration_seconds",
    help: "Wall-clock duration of the simd tx lockandmint mint CLI invocation.",
    registers: [registry],
    buckets: [0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30],
  });

  return {
    registry,
    eventsObserved,
    eventsProcessed,
    eventsFailed,
    queueDepth,
    e2eLatency,
    mintDuration,
  };
}

function startMetricsServer(metrics, port, host) {
  return new Promise((resolve, reject) => {
    const server = http.createServer(async (req, res) => {
      if (req.url === "/metrics") {
        res.setHeader("Content-Type", metrics.registry.contentType);
        res.end(await metrics.registry.metrics());
        return;
      }
      if (req.url === "/healthz") {
        res.setHeader("Content-Type", "application/json");
        res.end('{"status":"ok"}');
        return;
      }
      res.statusCode = 404;
      res.end();
    });
    server.once("error", reject);
    server.listen(port, host || "0.0.0.0", () => {
      resolve(server);
    });
  });
}

module.exports = { createMetrics, startMetricsServer };
