# benchmarks

Three local benchmarks that produce six of the paper's figures. Each
sub-directory ships with raw data (`results*.json`), a `plot.py` that
renders every figure for that benchmark, and a `caption.txt` per
figure that carries the IEEE-style caption text.

| Sub-directory       | Figures it produces                                                                                                                                              | Source of `results*.json`                  |
|---------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------|
| `crypto_micro/`     | Fig. 4 (signing latency vs msg size), Fig. 5 (verification vs msg size), Fig. 6 (concurrent signing) — and three additional figures used in the paper appendix. | Go bench at `crypto_micro/crypto_bench_test.go` (own `go.mod`, replace dirs to `../../cosmos`). Run with `go test -bench=. -benchtime=1x -run=^$`. |
| `storage_sim/`      | Fig. 8 (account-state growth)                                                                                                                                    | Storage simulator at `tools/storage_sim/main.go`. Pure-stdlib Go program; runs in seconds. |
| `block_packing/`    | Fig. 7 (block capacity)                                                                                                                                          | Block-packing analyser at `tools/block_packing/main.go`. Pure-stdlib Go program; runs in seconds. |

See [`../REPRODUCE.md`](../REPRODUCE.md) for the exact command per
figure.

## Reading the data

Each `results*.json` is human-readable. Crypto-micro `results.json` is
a list of `{operation, scheme, msg_size_bytes, ns_per_op,
allocs_per_op, bytes_per_op, …}` records — one record per Go
sub-benchmark. Storage-sim and block-packing JSONs are simulator
outputs with per-tx and per-block accounting.

## Re-running

```bash
# crypto_micro (parse_results.py reads raw_benchmark.txt and writes results.json; it takes no args)
cd crypto_micro && go test -bench=. -benchtime=1x -run=^$ > raw_benchmark.txt \
  && python3 parse_results.py && python3 plot.py

# storage_sim
cd ../../tools/storage_sim && go run . --out ../../benchmarks/storage_sim/
cd ../../benchmarks/storage_sim && python3 plot.py

# block_packing
cd ../../tools/block_packing && go run . > ../../benchmarks/block_packing/results.json
cd ../../benchmarks/block_packing && python3 plot.py
```

## Single Go run vs paper data

`crypto_bench_test.go` runs each sub-benchmark a single time
(`-benchtime=1x`) — that is what produced the data shipped here.
There is no run-to-run variance reported in the figures because
`testing.B` auto-tunes its own iteration count. Numbers will differ
slightly on different hardware; the magnitudes and ratios that the
paper draws conclusions from are stable.
