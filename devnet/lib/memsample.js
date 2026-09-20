// Sample how much memory proving actually uses, while it is happening.
//
// devnet/README.md quotes ~27.5 GB peak of a 28 GB ceiling for one SP1 Groth16
// proof, but that figure was read off a monitor by hand and no run ever
// recorded its own. So every committed relay result carries proving time and
// gas with no memory alongside, and a claim about memory cannot be checked
// against the run that produced it.
//
// Memory has to be sampled rather than read at the end: peak usage happens
// during the recursion phase, in the middle of the proof, and by the time the
// gRPC call returns the prover has already freed it. A single reading taken
// afterwards shows almost nothing.
//
// Two numbers are collected, because neither alone is the whole story:
//
//   proof-api's own usage   the prover process and any children it spawns.
//                           This is the figure that belongs next to a proving
//                           time: it is what THIS proof cost.
//   the machine's usage     total used memory and swap. The prover is not
//                           alone on the box -- geth, lighthouse and pqchaind
//                           are all running -- and it is the machine total
//                           that decides whether the next proof has headroom.
//
// Swap is tracked separately from resident memory and never folded into it.
// Proving on this host genuinely swaps, and a peak that is really 21 GB
// resident plus 6 GB swapped out is a different situation from 27 GB resident,
// even though both sum to the same number.
const fs = require("fs");
const path = require("path");

const KIB = 1024;

// /proc/<pid>/status reports in kB. Missing fields read as 0 rather than
// throwing: VmSwap is absent on a process that has never swapped.
function procStatus(pid) {
  let text;
  try {
    text = fs.readFileSync(`/proc/${pid}/status`, "utf8");
  } catch {
    return null;                       // process exited between scan and read
  }
  const field = (name) => {
    const m = text.match(new RegExp(`^${name}:\\s+(\\d+) kB$`, "m"));
    return m ? parseInt(m[1], 10) * KIB : 0;
  };
  return { rss: field("VmRSS"), swap: field("VmSwap") };
}

function meminfo() {
  const text = fs.readFileSync("/proc/meminfo", "utf8");
  const field = (name) => {
    const m = text.match(new RegExp(`^${name}:\\s+(\\d+) kB$`, "m"));
    return m ? parseInt(m[1], 10) * KIB : 0;
  };
  const memTotal = field("MemTotal");
  const swapTotal = field("SwapTotal");
  return {
    memTotal,
    swapTotal,
    // MemAvailable, not MemFree: page cache is reclaimable, so MemFree
    // understates what a process could still get and would make every reading
    // look like the machine is nearly full.
    memUsed: memTotal - field("MemAvailable"),
    swapUsed: swapTotal - field("SwapFree"),
  };
}

// Every descendant of `root`, root included. The prover may fork, and a
// reading that covered only the parent would miss whatever the child holds.
function processTree(root) {
  let pids;
  try {
    pids = fs.readdirSync("/proc").filter((d) => /^\d+$/.test(d)).map(Number);
  } catch {
    return [root];
  }
  const children = new Map();
  for (const pid of pids) {
    let stat;
    try {
      stat = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
    } catch {
      continue;                        // exited mid-scan; skip it
    }
    // The comm field is parenthesised and may itself contain spaces or
    // parentheses, so the fields after it are found from the LAST ')'.
    const after = stat.slice(stat.lastIndexOf(")") + 2).split(" ");
    const ppid = parseInt(after[1], 10);
    if (!Number.isNaN(ppid)) {
      if (!children.has(ppid)) children.set(ppid, []);
      children.get(ppid).push(pid);
    }
  }
  const out = [];
  const stack = [root];
  const seen = new Set();
  while (stack.length) {
    const pid = stack.pop();
    if (seen.has(pid)) continue;
    seen.add(pid);
    out.push(pid);
    for (const c of children.get(pid) || []) stack.push(c);
  }
  return out;
}

// proof-api's pid, from the file the bring-up script writes. Falling back to a
// name scan would be convenient but is not safe here: this host has run
// several proof-api instances, and attributing another one's memory to this
// proof would be worse than reporting nothing.
function proofApiPid(env) {
  const pidFile = path.join(env.DEVNET_DIR || "", "proof-api.pid");
  try {
    const pid = parseInt(fs.readFileSync(pidFile, "utf8").trim(), 10);
    if (!Number.isNaN(pid) && fs.existsSync(`/proc/${pid}`)) return pid;
  } catch { /* fall through */ }
  return null;
}

// Start sampling. Returns a handle whose stop() gives the peaks.
//
// Sampling never throws and never fails the relay around it: a measurement of
// the run must not be able to destroy the run. If the pid cannot be found, the
// machine-wide numbers are still collected and `pid` comes back null, which is
// recorded as-is so the gap is visible rather than silently filled.
function start(env, { intervalMs = 2000 } = {}) {
  const pid = proofApiPid(env);
  const startedAt = Date.now();
  const peak = {
    proofApiRssBytes: 0,
    proofApiSwapBytes: 0,
    systemUsedBytes: 0,
    systemSwapUsedBytes: 0,
  };
  let samples = 0;
  let failures = 0;
  const { memTotal, swapTotal } = meminfo();

  const tick = () => {
    try {
      if (pid !== null) {
        let rss = 0;
        let swap = 0;
        for (const p of processTree(pid)) {
          const s = procStatus(p);
          if (s) {
            rss += s.rss;
            swap += s.swap;
          }
        }
        if (rss > peak.proofApiRssBytes) peak.proofApiRssBytes = rss;
        if (swap > peak.proofApiSwapBytes) peak.proofApiSwapBytes = swap;
      }
      const m = meminfo();
      if (m.memUsed > peak.systemUsedBytes) peak.systemUsedBytes = m.memUsed;
      if (m.swapUsed > peak.systemSwapUsedBytes) peak.systemSwapUsedBytes = m.swapUsed;
      samples += 1;
    } catch {
      failures += 1;
    }
  };

  tick();                              // a baseline before the work starts
  const timer = setInterval(tick, intervalMs);
  // Do not hold the event loop open: the process should exit when the relay is
  // done, whether or not stop() was reached.
  if (timer.unref) timer.unref();

  return {
    pid,
    stop() {
      tick();                          // catch a peak in the final interval
      clearInterval(timer);
      const gib = (b) => Number((b / 1024 ** 3).toFixed(2));
      return {
        proofApiPid: pid,
        sampleIntervalMs: intervalMs,
        samples,
        sampleFailures: failures,
        sampledSeconds: Number(((Date.now() - startedAt) / 1000).toFixed(1)),
        peakProofApiRssBytes: peak.proofApiRssBytes,
        peakProofApiSwapBytes: peak.proofApiSwapBytes,
        peakSystemUsedBytes: peak.systemUsedBytes,
        peakSystemSwapUsedBytes: peak.systemSwapUsedBytes,
        peakProofApiRssGiB: gib(peak.proofApiRssBytes),
        peakProofApiSwapGiB: gib(peak.proofApiSwapBytes),
        peakSystemUsedGiB: gib(peak.systemUsedBytes),
        peakSystemSwapUsedGiB: gib(peak.systemSwapUsedBytes),
        memTotalGiB: gib(memTotal),
        swapTotalGiB: gib(swapTotal),
      };
    },
  };
}

module.exports = { start };
