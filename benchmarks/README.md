# benchmarks

Three local benchmarks that produce Figures 1 to 8 of the paper. Each
sub-directory ships with raw data (`results*.json`), a `plot.py` that
renders every figure for that benchmark, and a `caption.txt` per
figure that carries the IEEE-style caption text.

| Sub-directory       | Figures it produces                                                                                                                                              | Source of `results*.json`                  |
|---------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------|
| `crypto_micro/`     | Fig. 1 (key generation), Fig. 2 (signing vs message size), Fig. 3 (verification vs message size), Fig. 4 (concurrent signing), Fig. 5 (batch verification), Fig. 6 (memory per operation) | Go bench at `crypto_micro/crypto_bench_test.go` (own `go.mod`, stock Cosmos SDK v0.55). Run with `go test -bench=. -benchmem -count=10 -run='^$'`. |
| `storage_sim/`      | Fig. 7 (account-state growth from migrations) | Storage simulator at `tools/storage_sim/main.go`. Pure-stdlib Go program; runs in seconds. |
| `block_packing/`    | Fig. 8 (block capacity) | Block-packing analyser at `tools/block_packing/main.go`. Pure-stdlib Go program; runs in seconds. |

See [`../REPRODUCE.md`](../REPRODUCE.md) for the exact command per
figure.

Per-benchmark notes: [block_packing](block_packing/summary.md) ·
[storage_sim](storage_sim/summary.md).

## Reading the data

Each `results*.json` is human-readable. Crypto-micro `results.json` is
a list of `{operation, scheme, msg_size_bytes, ns_per_op,
allocs_per_op, bytes_per_op, …}` records — one record per Go
sub-benchmark. Storage-sim and block-packing JSONs are simulator
outputs with per-tx and per-block accounting.

## Re-running

```bash
# crypto_micro (parse_results.py reads raw_benchmark.txt and writes results.json; it takes no args)
cd crypto_micro && go test -bench=. -benchmem -count=10 -run='^$' -timeout=60m . > raw_benchmark.txt \
  && python3 parse_results.py && python3 plot.py

```

The simulator commands for `storage_sim` and `block_packing` are in
[REPRODUCE.md §4](../REPRODUCE.md#4-regenerate-the-raw-data-optional).

## How the timings are taken

Each sub-benchmark runs 10 times (`-count=10`), and `testing.B` picks its own
iteration count within each run; the parser keeps the median of the 10. The
figures show that median, without run-to-run spread. Absolute timings depend on
the hardware; the committed data comes from an AMD Ryzen 5 7600X with 6 logical
CPUs, which also sets where the concurrency figures stop scaling.

---

[Project README](../README.md) · [REPRODUCE](../REPRODUCE.md)
