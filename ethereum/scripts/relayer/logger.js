"use strict";

const LEVELS = { debug: 10, info: 20, warn: 30, error: 40 };
const LEVEL = LEVELS[(process.env.RELAYER_LOG_LEVEL || "info").toLowerCase()] || LEVELS.info;

function emit(level, msg, fields) {
  if (LEVELS[level] < LEVEL) return;
  const rec = Object.assign(
    { ts: new Date().toISOString(), level, msg },
    fields || {}
  );
  const stream = level === "error" ? process.stderr : process.stdout;
  stream.write(JSON.stringify(rec) + "\n");
}

function mkLogger(baseFields) {
  const base = baseFields || {};
  const bind = (level) => (msg, extra) =>
    emit(level, msg, Object.assign({}, base, extra || {}));
  return {
    debug: bind("debug"),
    info: bind("info"),
    warn: bind("warn"),
    error: bind("error"),
    child: (extra) => mkLogger(Object.assign({}, base, extra || {})),
  };
}

module.exports = { mkLogger };
