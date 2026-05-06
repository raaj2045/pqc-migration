// Command block_packing computes the maximum number of 1-in 1-out transfer
// transactions that fit in a Cosmos/CometBFT block for secp256k1 vs ML-DSA-44,
// at the default block size and scaled variants. Block-level overhead is
// modeled from the CometBFT constants so the numbers match what PrepareProposal
// actually sees as MaxTxBytes.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
)

// CometBFT block-overhead constants, from github.com/cometbft/cometbft/types/params.go
// and types/block.go. MaxDataBytes(maxBytes) subtracts these from the configured
// block-size cap to yield MaxTxBytes, which is the budget for transactions.
const (
	MaxHeaderBytes      int64 = 626
	MaxOverheadForBlock int64 = 11
	commitOverhead      int64 = 94  // MaxCommitOverheadBytes
	commitPerValidator  int64 = 109 // MaxCommitSigBytes
)

// CometBFTGenesisDefaultMaxBytes is the consensus_params.block.max_bytes value
// used when a genesis.json is generated without overrides (see
// github.com/cometbft/cometbft/types/params.go:DefaultBlockParams — 22_020_096
// bytes, i.e. 21 MiB). Confirmed in this repo at
// tests/fixtures/adr-024-coin-metadata_genesis.json:6.
const CometBFTGenesisDefaultMaxBytes int64 = 22020096

// Wire-size constants for a 1-in 1-out MsgSend transaction, modeled against the
// Cosmos SDK tx proto types. See tools/storage_sim/main.go for the derivation.
const (
	TxEnvelopeOverhead   = 110 // TxRaw + TxBody + AuthInfo + Fee + SignerInfo (ex pubkey)
	MsgSend1In1OutBytes  = 80  // MsgSend with 2 bech32 addrs + 1 Coin
	Secp256k1PubKeyBytes = 33
	Secp256k1SigBytes    = 64
	MLDSA44PubKeyBytes   = 1312
	MLDSA44SigBytes      = 2420
)

func maxCommitBytes(validators int) int64 {
	return commitOverhead + int64(validators)*commitPerValidator
}

// maxDataBytes mirrors CometBFT's MaxDataBytes: the bytes that transactions
// actually get to consume inside a block of size maxBytes.
func maxDataBytes(maxBytes, evidenceBytes int64, validators int) int64 {
	d := maxBytes - MaxOverheadForBlock - MaxHeaderBytes - maxCommitBytes(validators) - evidenceBytes
	if d < 0 {
		return 0
	}
	return d
}

func txSize(scheme string) (int64, error) {
	switch scheme {
	case "secp256k1":
		return TxEnvelopeOverhead + MsgSend1In1OutBytes + Secp256k1PubKeyBytes + Secp256k1SigBytes, nil
	case "mldsa44":
		return TxEnvelopeOverhead + MsgSend1In1OutBytes + MLDSA44PubKeyBytes + MLDSA44SigBytes, nil
	}
	return 0, fmt.Errorf("unknown scheme %q", scheme)
}

// packBlock simulates packing transactions of the given size into maxData
// bytes, one at a time, until the next tx would overflow. Returned values are
// (count, used_bytes).
func packBlock(txBytes, maxData int64) (int64, int64) {
	if txBytes <= 0 || maxData <= 0 {
		return 0, 0
	}
	var count, used int64
	for used+txBytes <= maxData {
		used += txBytes
		count++
	}
	return count, used
}

// readGenesisMaxBytes tries to load consensus_params.block.max_bytes from a
// genesis.json-like file. Returns (0, "", nil) if the file is absent so the
// caller can fall back to the CometBFT default.
func readGenesisMaxBytes(path string) (int64, string, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return 0, "", nil
		}
		return 0, "", err
	}
	var outer struct {
		ConsensusParams struct {
			Block struct {
				MaxBytes any `json:"max_bytes"` // string or number across versions
			} `json:"block"`
		} `json:"consensus_params"`
		Consensus struct {
			Params struct {
				Block struct {
					MaxBytes any `json:"max_bytes"`
				} `json:"block"`
			} `json:"params"`
		} `json:"consensus"`
	}
	if err := json.Unmarshal(raw, &outer); err != nil {
		return 0, "", fmt.Errorf("parse %s: %w", path, err)
	}
	for _, v := range []any{outer.ConsensusParams.Block.MaxBytes, outer.Consensus.Params.Block.MaxBytes} {
		switch x := v.(type) {
		case string:
			if x == "" {
				continue
			}
			n, err := strconv.ParseInt(x, 10, 64)
			if err != nil {
				return 0, "", fmt.Errorf("parse max_bytes %q: %w", x, err)
			}
			return n, path, nil
		case float64:
			return int64(x), path, nil
		}
	}
	return 0, "", nil
}

// resolveDefaultMaxBytes picks the max_bytes value used as the "default" config.
// Search order: --max-bytes flag, --genesis flag, config/genesis.json in CWD or
// user-provided dir, then the CometBFT genesis default.
func resolveDefaultMaxBytes(flagVal int64, genesisPath, configDir string) (int64, string) {
	if flagVal > 0 {
		return flagVal, "flag --max-bytes"
	}
	candidates := []string{}
	if genesisPath != "" {
		candidates = append(candidates, genesisPath)
	}
	if configDir != "" {
		candidates = append(candidates, filepath.Join(configDir, "genesis.json"))
	}
	candidates = append(candidates,
		"config/genesis.json",
		filepath.Join(os.Getenv("HOME"), ".simapp/config/genesis.json"),
	)
	for _, c := range candidates {
		if n, src, err := readGenesisMaxBytes(c); err == nil && n > 0 {
			return n, "genesis " + src
		}
	}
	return CometBFTGenesisDefaultMaxBytes, "CometBFT default (22_020_096 bytes, 21 MiB)"
}

type Result struct {
	Scheme              string  `json:"scheme"`
	BlockConfigLabel    string  `json:"block_config"`
	BlockMaxBytes       int64   `json:"block_max_bytes"`
	BlockMaxBytesSource string  `json:"block_max_bytes_source,omitempty"`
	ValidatorsAssumed   int     `json:"validators_assumed"`
	MaxDataBytes        int64   `json:"max_data_bytes"`
	TxSizeBytes         int64   `json:"tx_size_bytes"`
	MaxTxPerBlock       int64   `json:"max_tx_per_block"`
	AvgTxSizeBytes      int64   `json:"avg_tx_size_bytes"`
	UsedBytes           int64   `json:"used_bytes"`
	BlockUtilizationPct float64 `json:"block_utilization_pct"`
	WastedBytes         int64   `json:"wasted_bytes"`
}

func main() {
	maxBytesFlag := flag.Int64("max-bytes", 0, "override the 'default' block max_bytes (positive int)")
	genesisPath := flag.String("genesis", "", "path to a genesis.json to read consensus_params.block.max_bytes from")
	configDir := flag.String("config-dir", "", "path to a chain config dir (looks for config/genesis.json inside)")
	validators := flag.Int("validators", 100, "assumed validator count for MaxCommit overhead")
	output := flag.String("output", "results.json", "output JSON path")
	flag.Parse()

	baseMax, src := resolveDefaultMaxBytes(*maxBytesFlag, *genesisPath, *configDir)
	fmt.Fprintf(os.Stderr, "block_packing: base max_bytes = %d (%s)\n", baseMax, src)

	configs := []struct {
		label string
		bytes int64
	}{
		{"default", baseMax},
		{"2x default", baseMax * 2},
		{"4x default", baseMax * 4},
	}

	results := make([]Result, 0, len(configs)*2)
	for _, cfg := range configs {
		maxData := maxDataBytes(cfg.bytes, 0, *validators)
		for _, scheme := range []string{"secp256k1", "mldsa44"} {
			txS, err := txSize(scheme)
			if err != nil {
				fmt.Fprintln(os.Stderr, "error:", err)
				os.Exit(2)
			}
			count, used := packBlock(txS, maxData)
			wasted := cfg.bytes - used
			util := 0.0
			if cfg.bytes > 0 {
				util = float64(used) / float64(cfg.bytes) * 100
			}
			results = append(results, Result{
				Scheme:              scheme,
				BlockConfigLabel:    cfg.label,
				BlockMaxBytes:       cfg.bytes,
				BlockMaxBytesSource: src,
				ValidatorsAssumed:   *validators,
				MaxDataBytes:        maxData,
				TxSizeBytes:         txS,
				MaxTxPerBlock:       count,
				AvgTxSizeBytes:      txS,
				UsedBytes:           used,
				BlockUtilizationPct: util,
				WastedBytes:         wasted,
			})
		}
	}

	data, err := json.MarshalIndent(results, "", "  ")
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
	if err := os.WriteFile(*output, data, 0644); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
	for _, r := range results {
		fmt.Fprintf(os.Stderr,
			"  %-10s scheme=%-10s max_bytes=%-10d tx_size=%-5d tx_per_block=%-8d util=%.2f%% wasted=%d B\n",
			r.BlockConfigLabel, r.Scheme, r.BlockMaxBytes, r.TxSizeBytes,
			r.MaxTxPerBlock, r.BlockUtilizationPct, r.WastedBytes)
	}
	fmt.Fprintf(os.Stderr, "wrote %s (%d rows)\n", *output, len(results))
}
