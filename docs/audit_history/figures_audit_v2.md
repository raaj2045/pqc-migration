# Figure audit — v2 declutter pass

Twelve figures across four experiments were regenerated from raw data
(no PDF hand-editing) under a unified declutter rule set:

* All in-figure titles dropped — the caption file carries the title.
* Axis labels reduced to the minimal phrase plus units.
* Legends made small, frameless, and use bare scheme names
  (`secp256k1`, `ML-DSA-44`); FIPS / ECDSA parentheticals are stated
  once in the caption only.
* Reference lines re-coloured to light gray dashed and labeled at the
  right edge instead of in a separate legend entry.
* Per-element data labels limited to one — the absolute value on bars,
  no per-point labels on lines (the axis carries that).
* Three-subplot layout used for the four validator_scaling_v2 figures
  (one panel per N), sharing the y-axis across panels.
* Colours pinned to the paper-wide palette: secp256k1 = `#1f77b4`
  (blue), ML-DSA-44 = `#d62728` (red).
* Tick density trimmed to roughly 4–6 majors per axis.

Source-of-truth scripts (running them reproduces every figure exactly):

* `cosmos-latest/benchmarks/crypto_micro/plot.py`
* `cosmos-latest/benchmarks/storage_sim/plot.py`
* `cosmos-latest/benchmarks/block_packing/plot.py`
* `experiments/validator_scaling_v2/aggregate.py`

Caption files live next to each PDF as `<figure>.caption.txt`.

---

## Per-figure changes

### `cosmos-latest/benchmarks/crypto_micro/`

**fig_keygen_comparison.pdf** — bar chart, two bars (one per scheme).
Removed: in-figure title, the `93× slower` ratio annotation, the
`KeyGen` x-axis label, the `(ECDSA)` and `(FIPS 204)` legend
parentheticals. Kept: absolute microsecond value above each bar, log
ordinate. Takeaway visible at a glance: ML-DSA-44 bar reaches ~52 μs
versus a near-floor secp256k1 bar.

**fig_signing_by_msg_size.pdf** — log-log line plot, four points per
scheme. Removed: in-figure title, fake ±5 % shaded confidence band,
verbose axis labels (`Message Size (bytes, log scale)` →
`Message size (log)`). Kept: explicit tick labels at 100 B / 1 KiB /
10 KiB / 100 KiB. Takeaway visible at a glance: red ML-DSA-44 line
sits ~3.5× above the blue secp256k1 line across the entire range.

**fig_verification_by_msg_size.pdf** — same shape and changes as
fig_signing. Takeaway visible at a glance: the colours flip — blue
secp256k1 line sits above red ML-DSA-44 by ~3×.

**fig_concurrent_signing.pdf** — log-y line plot, four points per
scheme. Removed: in-figure title, the ideal-linear-scaling reference
line that the previous draft added (the sub-linear curve is already
visible without it), wordy axis labels. Kept: bare scheme names in a
frameless legend. Takeaway visible at a glance: both lines bend
flatward past goroutines = 8 (the host has 6 physical cores).

**fig_batch_verification.pdf** — single-panel log-log line plot
(previous draft was two-panel). Removed: in-figure title, the total-
batch-time panel (the headline is amortized per-signature cost, the
total is in results.json), fake ±5 % CI bands, the `(ECDSA)` /
`(FIPS 204)` legend tags. Kept: three batch-size points per scheme.
Takeaway visible at a glance: both lines are roughly horizontal, i.e.
neither scheme amortizes meaningfully with batching.

**fig_memory_allocs.pdf** — two side-by-side bar charts (bytes/op
left, allocs/op right). Removed: in-figure suptitle, panel titles
(`(a) Bytes...` / `(b) Allocs...`), the long `(1 KiB msg)` tick
labels (now `(1 KiB)`), the `(ECDSA)` / `(FIPS 204)` legend tags.
Kept: absolute counts on each bar, log ordinate. Takeaway visible at
a glance: every red ML-DSA-44 bar towers over its blue secp256k1
counterpart by 1–4 decades.

### `cosmos-latest/benchmarks/storage_sim/`

**fig_state_growth.pdf** — log-log line plot, two lines. Removed:
in-figure title, the three vertical reference lines at 100 K / 1 M /
10 M, the bracketed text-box ratio annotation in the bottom-right, the
`(ECDSA)` / `(FIPS 204)` legend tags, the verbose y-axis units
(`On-chain account-state size (MiB, log scale)` →
`Account-state size (MiB, log)`). Kept: a single double-headed
arrow at the rightmost data point bridging the two lines, labeled
`10.62×` — the only ratio annotation in the figure. Takeaway visible
at a glance: red line cleanly above blue line, ratio shown once at the
right edge.

### `cosmos-latest/benchmarks/block_packing/`

**fig_block_capacity.pdf** — grouped bar chart, three groups (4 / 8 /
16 MiB), two bars per group. Removed: in-figure title, the per-group
`13.67× cut` ratio annotations, the verbose x-axis label
(`Block size configuration (consensus_params.block.max_bytes)` →
`Block size limit`), the `default` / `2x default` / `4x default`
text in tick labels (now just `4 MiB` / `8 MiB` / `16 MiB`), the
`(ECDSA)` / `(PQ)` legend tags, the legend frame. Kept: absolute
counts above each bar, log ordinate. Takeaway visible at a glance:
ML-DSA-44 bars are ~14× shorter than secp256k1 across all three
groups; the constant ratio shows in the parallel bar pairs.

### `experiments/validator_scaling_v2/`

All four figures changed from a single panel with six lines (3 N × 2
schemes) to a row of three subplots (one per N) with two lines each.
Subplot titles are bare `N=4` / `N=7` / `N=16`. The single shared
legend lives below the row so it never overlaps data.

**fig_saturation_curve.pdf** — three subplots, achieved-vs-submitted
TPS. Removed: in-figure title, the `ideal y=x` reference line (was
making the saturation point harder to spot, not easier), the verbose
axis labels (`Submitted load (target tx/s, log scale)` →
`Submitted (tx/s, log)`). Added: a per-scheme dotted horizontal
saturation line with a right-edge value label
(e.g. `67 tx/s` for secp at N=4) so the chain ceiling is visible
without reading the caption. Takeaway visible at a glance: in every
subplot, both lines plateau and the red ceiling sits below the blue
ceiling.

**fig_p99_vs_rate.pdf** — three subplots, log-y. Removed: in-figure
title, in-legend `10 s threshold` entry. Added: a single light gray
dashed reference line at y = 10 s with a right-edge label
`10 s strict criteria`. Takeaway visible at a glance: the curves cross
the gray line between rate = 50 and rate = 100 in every subplot.

**fig_committed_pct.pdf** — three subplots, linear y in [0, 105].
Removed: in-figure title, in-legend `90 % threshold` entry. Added: a
single light gray dashed reference line at y = 90 with a right-edge
label `90 % strict criteria`. Takeaway visible at a glance: the
curves drop off the cliff between rate = 100 and rate = 200; in the
N=16 panel the red ML-DSA-44 line dips below the gray line one tier
earlier than the blue secp256k1 line.

**fig_peak_cpu.pdf** — three subplots, linear y in [0, 105].
Removed: in-figure title, in-legend `90 % cap` entry, the verbose
y-axis label (`Peak validator CPU % (per 1-core container)` →
`Peak validator CPU (%)`). Added: a single light gray dashed reference
line at y = 90 with a right-edge label `90 % strict criteria`.
Takeaway visible at a glance: every value sits well below the gray
line; the red ML-DSA-44 line consistently runs above the blue
secp256k1 line at low submitted rates and the relationship can
invert at saturating rates.

---

## Confirming each takeaway

The bar set for "takeaway visible at a glance" in each entry above
states the visual pattern that survives the declutter. None of those
patterns required the elements that were removed — every removal
either duplicated information already in the caption, duplicated
information already in the axes, or competed for visual attention
with the lines or bars themselves. Captions were tightened in
parallel so any reviewer reading the caption alone can reconstruct
hardware, run count, units, and the headline ratio for that figure.
