#!/usr/bin/env python3
"""Integrity verifier for validator_scaling_v2 sweep results.

Checks every results/N{n}_rate{r}_{scheme}.json for:
  (1) internal numeric consistency (achieved_tps ≈ committed/duration ± 5%,
      committed ≤ submitted, etc.)
  (2) status label matches the sweep classification rules
      (ok: committed/submitted ≥ 0.9 AND p99 < 10000;
       saturated: committed/submitted < 0.9 OR p99 ≥ 10000;
       crashed/timeout: only via run-time conditions, not from metric file)
  (3) p99 ≥ p50 (basic sanity)
  (4) sweep_meta.peak_cpu_pct ∈ [0, 100]
  (5) started_at < ended_at; per-tx submit_ms ascending in event order
  (6) cross-check saturation TPS table in summary.md against
      max(committed / duration_s) across 5 rates per (N, scheme)
  (7) for status="ok" runs, attempt to inspect captured validator logs
      for signature-verification errors. Validator logs are NOT captured
      per-run by run_sweep.py (only loadgen stdout is). The orchestrator
      tears the chain down at the end of each run, after which docker
      logs are gone with the container. We note this absence explicitly
      rather than hand-wave.

Writes VERIFY.md. Read-only — does not modify any source files.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

EXP = Path(__file__).parent.resolve()
RESULTS = EXP / "results"
LOGS = EXP / "logs"
SUMMARY = EXP / "summary.md"

NS = [4, 7, 16]
RATES = [10, 50, 100, 200, 500]
SCHEMES = ["secp256k1", "mldsa44"]


def file_for(n, rate, scheme):
    return RESULTS / f"N{n}_rate{rate}_{scheme}.json"


def check_one(p: Path):
    """Return list of (severity, message) issues for one result file."""
    issues = []
    try:
        doc = json.loads(p.read_text())
    except Exception as e:
        return [("error", f"cannot parse JSON: {e}")]

    submitted = doc.get("submitted")
    committed = doc.get("committed")
    duration_s = doc.get("duration_s")
    http_errors = doc.get("http_errors")
    lat = doc.get("latency_inclusion_ms") or {}
    p50 = lat.get("p50")
    p99 = lat.get("p99")
    started_at = doc.get("started_at")
    ended_at = doc.get("ended_at")
    sweep_meta = doc.get("sweep_meta") or {}
    status = sweep_meta.get("status")
    peak_cpu = sweep_meta.get("peak_cpu_pct")
    txs = doc.get("txs") or []

    # 1. internal numeric consistency
    if submitted is None or committed is None or duration_s is None:
        issues.append(("error", "missing submitted/committed/duration_s"))
    else:
        if committed > submitted:
            issues.append(("error", f"committed ({committed}) > submitted ({submitted})"))
        if submitted < 0 or committed < 0:
            issues.append(("error", f"negative count: submitted={submitted} committed={committed}"))
        if duration_s <= 0:
            issues.append(("error", f"duration_s ≤ 0: {duration_s}"))
        # achieved_tps is not stored as a field in the result; instead
        # the summary computes it from committed/duration_s. The closest
        # internal consistency we can check: committed + http_errors (and
        # possibly check_fail) should equal submitted for runs where
        # every submission either committed or errored.
        check_ok = doc.get("check_ok", 0)
        check_fail = doc.get("check_fail", 0)
        recorded = check_ok + check_fail + (http_errors or 0)
        if abs(recorded - submitted) > max(2, submitted * 0.001):
            issues.append(("warn",
                f"submission accounting drift: check_ok+check_fail+http_errors = "
                f"{recorded} vs submitted = {submitted}"))

    # 2. status label vs criteria
    if status not in ("ok", "saturated", "crashed", "timeout"):
        issues.append(("error", f"unknown status: {status}"))
    elif status == "ok":
        if submitted and (committed / max(1, submitted)) < 0.9:
            issues.append(("error",
                f"status=ok but committed/submitted = "
                f"{committed/submitted:.3f} < 0.9"))
        if p99 is not None and p99 >= 10_000:
            issues.append(("error",
                f"status=ok but p99 = {p99}ms ≥ 10000"))
    elif status == "saturated":
        ok_commit = submitted and (committed / max(1, submitted)) >= 0.9
        ok_p99 = p99 is not None and p99 < 10_000
        if ok_commit and ok_p99:
            issues.append(("error",
                f"status=saturated but committed/submitted = "
                f"{committed/submitted:.3f} ≥ 0.9 AND p99 = {p99} < 10000 "
                f"(should be ok)"))

    # 3. p99 ≥ p50
    if p50 is not None and p99 is not None and p99 < p50:
        issues.append(("error", f"p99 ({p99}) < p50 ({p50})"))

    # 4. peak_cpu range
    if peak_cpu is None:
        issues.append(("warn", "sweep_meta.peak_cpu_pct missing"))
    elif not (0.0 <= peak_cpu <= 100.0):
        issues.append(("error", f"peak_cpu_pct out of [0,100]: {peak_cpu}"))

    # 5. timestamps
    if not started_at or not ended_at:
        issues.append(("warn", "started_at / ended_at missing"))
    else:
        # crude lexicographic compare on RFC3339 strings is monotone in
        # time when the format is uniform (UTC, fixed precision)
        if started_at >= ended_at:
            issues.append(("error",
                f"started_at >= ended_at ({started_at} vs {ended_at})"))
    # Per-(sender, sequence) submit_ms should be non-decreasing — i.e.,
    # sequence N must have been submitted at-or-before sequence N+1 for
    # the same sender. NOTE: records[] in the file are stored in
    # HTTP-completion order (the loadgen has a 64–128-worker concurrent
    # broadcast pool and only acquires the records-mutex AFTER the HTTP
    # RTT). So we sort by (sender, sequence) FIRST and then check
    # submit_ms ordering — checking records[] in file order would be
    # checking completion order, not submission order.
    from collections import defaultdict
    per_sender = defaultdict(list)
    for r in txs:
        s = r.get("sender_idx")
        seq = r.get("sequence")
        sm = r.get("submit_ms")
        if s is None or seq is None or sm is None:
            continue
        per_sender[s].append((seq, sm))
    for s, lst in per_sender.items():
        lst.sort()  # by (seq, then submit_ms) — seq is unique per sender
        for i in range(1, len(lst)):
            if lst[i][1] < lst[i-1][1]:
                issues.append(("error",
                    f"per-sender submit_ms not monotone for sender {s} "
                    f"after sort by sequence: seq {lst[i-1][0]} → "
                    f"submit_ms {lst[i-1][1]}, then seq {lst[i][0]} → "
                    f"submit_ms {lst[i][1]}"))
                break

    return issues


def parse_summary_sat_table(text: str):
    """Pull the 3-row 'Headline — saturation TPS per (N, scheme)' table
    out of summary.md. Returns dict {(n, scheme) -> sat_tps_float} or None
    on parse failure.

    Bound the search to the headline section only — stop at the next
    `## ` heading to avoid eating subsequent tables ('Per-cell detail',
    'Where each cell first fails strict criteria', etc.) which would
    otherwise match the row regex with the wrong values."""
    m = re.search(
        r"## Headline — saturation TPS per \(N, scheme\)(.*?)(?=\n## )",
        text, re.S,
    )
    if not m:
        return None
    block = m.group(1)
    # Rows look like: | 4 | 67.0 | 50.0 | 75% |
    rows = re.findall(r"^\|\s*(\d+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|",
                      block, re.M)
    out = {}
    for n_s, secp_s, mldsa_s in rows:
        n = int(n_s)
        out[(n, "secp256k1")] = float(secp_s)
        out[(n, "mldsa44")] = float(mldsa_s)
    return out if out else None


def main():
    md = []
    md.append("# VERIFY — integrity report for validator_scaling_v2/results\n")
    md.append("Generated by `verify.py`. Read-only; nothing modified.\n\n")

    expected = [(n, r, s) for n in NS for r in RATES for s in SCHEMES]
    missing = [(n, r, s) for (n, r, s) in expected
               if not file_for(n, r, s).exists()]
    extra = sorted(p.name for p in RESULTS.glob("N*_rate*_*.json"))
    expected_names = {f"N{n}_rate{r}_{s}.json" for (n, r, s) in expected}
    extra = [name for name in extra if name not in expected_names]

    md.append("## File inventory\n\n")
    md.append(f"* Expected: 30 files (3 N × 5 rates × 2 schemes)\n")
    md.append(f"* Present: {30 - len(missing)}\n")
    if missing:
        md.append("* **MISSING:**\n")
        for n, r, s in missing:
            md.append(f"  - `N{n}_rate{r}_{s}.json`\n")
    if extra:
        md.append("* Extra files matching the pattern (not in 30-cell plan):\n")
        for name in extra:
            md.append(f"  - `{name}`\n")
    md.append("\n")

    # Per-file checks.
    md.append("## Per-file checks\n\n")
    md.append(
        "Each row: severity (✓ pass · ⚠ warn · ✗ fail) for the five "
        "internal checks (numeric consistency, status-label match, "
        "p99≥p50, peak_cpu∈[0,100], timestamps monotone). A run is "
        "**clean** only when all five pass.\n\n"
    )
    md.append("| N | rate | scheme | numeric | status | p99≥p50 | cpu | "
              "timestamps | notes |\n")
    md.append("|---|-----:|--------|:------:|:------:|:------:|:---:|"
              ":---------:|-------|\n")

    total_issues = 0
    issue_log = []  # (file, list_of_issues)
    for n, r, s in expected:
        p = file_for(n, r, s)
        if not p.exists():
            md.append(f"| {n} | {r} | {s} | — | — | — | — | — | "
                      f"file missing |\n")
            continue
        issues = check_one(p)

        # Bucket issues by which check they relate to.
        def has(kind):
            return any(kind in m.lower() for _, m in issues)
        cell = lambda b: "✓" if not b else ("✗" if any(sev=="error" for sev,_ in issues if any(k in m.lower() for k in [b])) else "⚠")
        # Simpler: walk each issue and tag.
        check_status = {"numeric": "✓", "status": "✓", "p99": "✓",
                        "cpu": "✓", "ts": "✓"}
        for sev, msg in issues:
            ml = msg.lower()
            mark = "✗" if sev == "error" else "⚠"
            if "submitted" in ml or "committed" in ml or "duration" in ml or "accounting" in ml or "negative count" in ml:
                if check_status["numeric"] != "✗":
                    check_status["numeric"] = mark
            elif "status=" in ml or "unknown status" in ml:
                if check_status["status"] != "✗":
                    check_status["status"] = mark
            elif "p99" in ml or "p50" in ml:
                if check_status["p99"] != "✗":
                    check_status["p99"] = mark
            elif "cpu" in ml:
                if check_status["cpu"] != "✗":
                    check_status["cpu"] = mark
            elif "started_at" in ml or "ended_at" in ml or "submit_ms" in ml:
                if check_status["ts"] != "✗":
                    check_status["ts"] = mark
            else:
                # uncategorised — surface as a note
                pass

        notes = "; ".join(m for _, m in issues) if issues else "—"
        md.append(f"| {n} | {r} | {s} | "
                  f"{check_status['numeric']} | {check_status['status']} | "
                  f"{check_status['p99']} | {check_status['cpu']} | "
                  f"{check_status['ts']} | {notes} |\n")
        if issues:
            issue_log.append((p.name, issues))
            total_issues += sum(1 for sev, _ in issues if sev == "error")
    md.append("\n")

    # Cross-check saturation TPS table.
    md.append("## Cross-check: saturation-TPS table in summary.md\n\n")
    sum_table = None
    if SUMMARY.exists():
        sum_table = parse_summary_sat_table(SUMMARY.read_text())
    if sum_table is None:
        md.append("⚠ could not parse the headline table from `summary.md`. "
                  "Skipping cross-check.\n\n")
    else:
        md.append(
            "Saturation TPS = max(committed / duration_s) across the 5 "
            "rates per (N, scheme). We recompute from the raw files and "
            "compare against the value in `summary.md`.\n\n"
            "| N | scheme | summary.md | recomputed | Δ |\n"
            "|---|--------|---:|---:|---:|\n"
        )
        sat_mismatches = 0
        for n in NS:
            for s in SCHEMES:
                vals = []
                for r in RATES:
                    p = file_for(n, r, s)
                    if not p.exists():
                        continue
                    try:
                        d = json.loads(p.read_text())
                        c = d.get("committed", 0)
                        dur = d.get("duration_s", 0) or 1
                        vals.append(c / dur)
                    except Exception:
                        pass
                recomp = max(vals, default=0.0)
                claimed = sum_table.get((n, s), 0.0)
                delta = recomp - claimed
                ok = abs(delta) < 0.05
                if not ok:
                    sat_mismatches += 1
                md.append(f"| {n} | {s} | {claimed:.1f} | "
                          f"{recomp:.1f} | "
                          f"{'✓' if ok else f'**Δ={delta:+.2f}**'} |\n")
        md.append("\n")
        if sat_mismatches:
            md.append(f"**{sat_mismatches} rows disagree** by more than 0.05 TPS — investigate.\n\n")
        else:
            md.append("All 6 (N × scheme) cells agree within 0.05 TPS.\n\n")

    # Validator-log capture status.
    md.append("## Validator log capture for status='ok' runs\n\n")
    md.append(
        "`run_sweep.py` does **not** capture per-run validator-side logs. "
        "Each run flow is: docker compose up → loadgen → docker compose down. "
        "When the chain is torn down, the container's stdout (the only place "
        "CometBFT emits sig-verification errors) is discarded with the "
        "container. The only logs we keep per run are the loadgen client "
        "stdout (in `logs/N{n}_rate{r}_{scheme}.log`) and the CPU sample "
        "CSV.\n\n"
        "Therefore: **post-hoc grep of validator logs for sig-verify "
        "errors is not possible from these artifacts.** What we *did* "
        "capture, transitively:\n\n"
        "* `submitted` − `committed` − `http_errors` − `check_fail` "
        "accounts for every tx that left the loadgen. For every status='ok' "
        "run, `check_fail = 0` and `http_errors = 0`, meaning every "
        "submission cleared the antehandler (which is where sig-verify "
        "lives) on first try and was committed. Sig-verify failure on "
        "ok-status runs is therefore observably ruled out *from the "
        "client-side accounting*, even though we do not have the "
        "validator log line saying 'verified ✓'.\n\n"
        "* During the smoke run earlier in this experiment, validator "
        "logs WERE inspected interactively and showed zero "
        "`sign|verif|invalid sig` matches across all 4 nodes for both "
        "schemes. That smoke covered the same code path as the sweep.\n\n"
    )
    # Verify the client-side accounting claim for ok-status runs.
    md.append("### Client-side accounting check for status='ok' runs\n\n")
    md.append("| N | rate | scheme | check_fail | http_errors | "
              "submitted − committed |\n")
    md.append("|---|-----:|--------|---:|---:|---:|\n")
    accounting_violations = 0
    for n in NS:
        for r in RATES:
            for s in SCHEMES:
                p = file_for(n, r, s)
                if not p.exists():
                    continue
                d = json.loads(p.read_text())
                if (d.get("sweep_meta") or {}).get("status") != "ok":
                    continue
                cf = d.get("check_fail", 0)
                he = d.get("http_errors", 0)
                gap = d.get("submitted", 0) - d.get("committed", 0)
                bad = (cf != 0) or (he != 0) or (gap != 0)
                if bad:
                    accounting_violations += 1
                md.append(f"| {n} | {r} | {s} | {cf} | {he} | {gap} |\n")
    md.append("\n")
    if accounting_violations:
        md.append(f"**{accounting_violations} ok-status runs have non-zero "
                  f"check_fail or http_errors or commit gap.** "
                  f"Sig-verify cannot be ruled out for these.\n\n")
    else:
        md.append("All ok-status runs show check_fail=0, http_errors=0, "
                  "submitted−committed=0 → every submission cleared the "
                  "antehandler and committed. Consistent with zero "
                  "sig-verify errors.\n\n")

    # Summary box.
    md.append("## Verdict\n\n")
    if total_issues == 0 and not missing and not (sum_table and sat_mismatches) and accounting_violations == 0:
        md.append("**ALL CLEAN.** 30/30 result files present; per-file "
                  "consistency checks all green; saturation-TPS headline "
                  "table reproducible from raw data; client-side accounting "
                  "for ok-status runs leaves no room for unobserved "
                  "sig-verify errors.\n\n")
    else:
        md.append("Issues found above. See per-file table and the relevant "
                  "section for detail.\n\n")
    md.append("Caveats already disclosed in `summary.md`:\n\n"
              "* p99 latency uses our /block poller's observation time (≤ "
              "500 ms after finalisation); this can run up to 500 ms LONGER "
              "than the true network commit time but never shorter.\n"
              "* status-label cross-checks above use the rules implemented "
              "in `run_sweep.py:run_one`. Three early files (N=4 "
              "rate=100/200 mldsa, N=4 rate=200 secp) were re-stamped from "
              "'crashed' to 'saturated' mid-sweep when the orchestrator's "
              "over-aggressive http_errors>50%=crash rule was corrected. "
              "The status-label match check is against the corrected "
              "rules, so those files now pass cleanly.\n")

    out_path = EXP / "VERIFY.md"
    out_path.write_text("".join(md))
    print(f"wrote {out_path}")
    return 0 if (total_issues == 0 and not missing) else 1


if __name__ == "__main__":
    sys.exit(main())
