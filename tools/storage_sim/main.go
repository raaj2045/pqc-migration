// Command storage_sim simulates on-chain state growth for secp256k1 and
// ML-DSA-44 signature schemes without running a live chain. It models per-tx
// wire size and per-account state size, sampling a log-spaced time series.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"math/rand"
	"os"
	"sort"
	"strconv"
	"strings"
)

// Signature-scheme byte sizes.
//
// secp256k1: compressed pubkey (33B) and canonical 64B signature as used by
// Cosmos SDK keys (crypto/keys/secp256k1). DER-encoded signatures can be up to
// ~72B on other stacks, but Cosmos serializes the 64B compact form.
//
// ML-DSA-44: parameter set defined in NIST FIPS 204. Sizes match
// github.com/cloudflare/circl/sign/mldsa/mldsa44 (PublicKeySize=1312,
// SignatureSize=2420). Retained at the ML-DSA-44 parameter set the published
// figures were measured against; see docs/formal-verification-case-study/.
const (
	Secp256k1PubKeyBytes = 33
	Secp256k1SigBytes    = 64
	MLDSA44PubKeyBytes   = 1312
	MLDSA44SigBytes      = 2420
)

// TxEnvelopeOverhead is the fixed per-tx wire overhead of a Cosmos SDK TxRaw,
// excluding the pubkey bytes, signature bytes, and the message payload. It
// sums tag/length bytes from the SDK proto definitions:
//
//   TxRaw wrapper (tags for body_bytes, auth_info_bytes, signatures):   ~10 B
//   TxBody tags (messages, memo=empty, extension_options=empty):        ~10 B
//   AuthInfo tags (signer_infos, fee):                                  ~8  B
//   SignerInfo: Any{type_url="/cosmos.crypto.*.PubKey"} + ModeInfo +
//               sequence varint (pubkey bytes counted separately):      ~46 B
//   Fee: gas_limit varint + amount=[{denom,amount}]:                    ~36 B
//
// See github.com/cosmos/cosmos-sdk/proto/cosmos/tx/v1beta1/tx.proto and the
// generated types in types/tx/tx.pb.go.
const TxEnvelopeOverhead = 110

// AccountStateOverhead is the per-account IAVL storage cost excluding the
// pubkey bytes: IAVL leaf key (account-store prefix + 20B address), leaf hash
// bookkeeping, and serialized BaseAccount fields (address bech32 string, Any
// type_url for pubkey, account_number varint, sequence varint).
// See github.com/cosmos/cosmos-sdk/x/auth/types/account.pb.go.
const AccountStateOverhead = 100

// MsgBodySizes lists serialized bytes for common Cosmos SDK messages at
// typical field values. Values are conservative averages measured against the
// generated proto types in the SDK (x/bank, x/staking, x/gov, x/wasm, and a
// custom bridge module for "migration").
//
//   transfer  (MsgSend):              ~80  B (2 bech32 addresses + 1 Coin)
//   migration (MsgMigrate/RedeemWETH): ~200 B (includes payload bytes)
//   stake     (MsgDelegate):           ~95  B (2 addresses + 1 Coin)
//   gov       (MsgVote):               ~55  B (proposal_id + voter + option)
var MsgBodySizes = map[string]int{
	"transfer":  80,
	"migration": 200,
	"stake":     95,
	"gov":       55,
}

// Account growth model: unique(n) = ceil(K * n^alpha). Sub-linear growth is
// observed on every production Cosmos chain — new-signer ratio drops from
// ~100% early in history to 1-2% in mature operation. Calibration targets:
//
//   unique(100k)  ≈ 12.5 k  (12.5 % of txs are first-seen)
//   unique(1M)    ≈ 79   k  ( 7.9 % )
//   unique(10M)   ≈ 500  k  ( 5.0 % )
//
// Tunable via --account-alpha / --account-k flags for sensitivity analysis.
const (
	defaultAccountAlpha = 0.8
	defaultAccountK     = 1.256 // 500_000 / 10_000_000^0.8
)

type Sample struct {
	TxCount         int64 `json:"tx_count"`
	TotalStateBytes int64 `json:"total_state_bytes"`
	TotalTxBytes    int64 `json:"total_tx_bytes"`
	UniqueAccounts  int64 `json:"unique_accounts"`
}

type SimResult struct {
	Scheme              string   `json:"scheme"`
	NumTx               int64    `json:"num_tx"`
	TxMix               string   `json:"tx_mix"`
	PubkeyBytes         int64    `json:"pubkey_bytes"`
	SignatureBytes      int64    `json:"signature_bytes"`
	EnvelopeOverhead    int64    `json:"envelope_overhead_bytes"`
	AccountOverhead     int64    `json:"account_overhead_bytes"`
	FinalStateBytes     int64    `json:"final_state_bytes"`
	FinalTxBytes        int64    `json:"final_tx_bytes"`
	FinalUniqueAccounts int64    `json:"final_unique_accounts"`
	Series              []Sample `json:"series"`
}

func parseMix(s string) (map[string]float64, []string, error) {
	mix := make(map[string]float64)
	names := []string{}
	total := 0.0
	for _, p := range strings.Split(s, ",") {
		kv := strings.SplitN(strings.TrimSpace(p), ":", 2)
		if len(kv) != 2 {
			return nil, nil, fmt.Errorf("invalid mix entry %q", p)
		}
		name := strings.TrimSpace(kv[0])
		pct, err := strconv.ParseFloat(strings.TrimSpace(kv[1]), 64)
		if err != nil {
			return nil, nil, fmt.Errorf("bad percent in %q: %v", p, err)
		}
		if _, ok := MsgBodySizes[name]; !ok {
			return nil, nil, fmt.Errorf("unknown msg type %q (known: transfer, migration, stake, gov)", name)
		}
		mix[name] = pct
		names = append(names, name)
		total += pct
	}
	if math.Abs(total-100.0) > 0.01 {
		return nil, nil, fmt.Errorf("tx-mix percentages must sum to 100, got %v", total)
	}
	return mix, names, nil
}

func schemeSizes(s string) (pub, sig int, err error) {
	switch s {
	case "secp256k1":
		return Secp256k1PubKeyBytes, Secp256k1SigBytes, nil
	case "mldsa44":
		return MLDSA44PubKeyBytes, MLDSA44SigBytes, nil
	}
	return 0, 0, fmt.Errorf("unknown scheme %q (want secp256k1 or mldsa44)", s)
}

// logSamplePoints returns unique, sorted checkpoint tx counts, log-spaced from
// 1 to max with roughly n samples. Used to bound the JSON output size at large
// --num-tx without losing shape on a log-x plot.
func logSamplePoints(max int64, n int) []int64 {
	if max <= 0 {
		return nil
	}
	if max <= int64(n) {
		out := make([]int64, max)
		for i := range out {
			out[i] = int64(i) + 1
		}
		return out
	}
	set := make(map[int64]struct{})
	for i := 0; i < n; i++ {
		t := float64(i) / float64(n-1)
		p := int64(math.Round(math.Pow(float64(max), t)))
		if p < 1 {
			p = 1
		}
		if p > max {
			p = max
		}
		set[p] = struct{}{}
	}
	set[max] = struct{}{}
	out := make([]int64, 0, len(set))
	for k := range set {
		out = append(out, k)
	}
	sort.Slice(out, func(i, j int) bool { return out[i] < out[j] })
	return out
}

type Simulator struct {
	numTx           int64
	pubKeySize      int64
	sigSize         int64
	mixNames        []string
	mixCum          []float64
	accountAlpha    float64
	accountK        float64
	rng             *rand.Rand
	txCount         int64
	totalTxBytes    int64
	totalStateBytes int64
	uniqueAccounts  int64
}

func newSim(scheme string, numTx int64, mix map[string]float64, names []string, alpha, k float64, seed int64) (*Simulator, error) {
	pub, sig, err := schemeSizes(scheme)
	if err != nil {
		return nil, err
	}
	cum := make([]float64, len(names))
	acc := 0.0
	for i, n := range names {
		acc += mix[n]
		cum[i] = acc
	}
	return &Simulator{
		numTx:        numTx,
		pubKeySize:   int64(pub),
		sigSize:      int64(sig),
		mixNames:     names,
		mixCum:       cum,
		accountAlpha: alpha,
		accountK:     k,
		rng:          rand.New(rand.NewSource(seed)),
	}, nil
}

func (s *Simulator) pickMsg() string {
	r := s.rng.Float64() * 100.0
	for i, c := range s.mixCum {
		if r <= c {
			return s.mixNames[i]
		}
	}
	return s.mixNames[len(s.mixNames)-1]
}

func (s *Simulator) expectedAccounts(n int64) int64 {
	if n <= 0 {
		return 0
	}
	v := s.accountK * math.Pow(float64(n), s.accountAlpha)
	a := int64(math.Floor(v))
	if a < 1 {
		return 1
	}
	return a
}

func (s *Simulator) step() {
	msgSize := MsgBodySizes[s.pickMsg()]
	s.totalTxBytes += int64(TxEnvelopeOverhead) + int64(msgSize) + s.pubKeySize + s.sigSize
	s.txCount++
	target := s.expectedAccounts(s.txCount)
	if target > s.uniqueAccounts {
		delta := target - s.uniqueAccounts
		s.uniqueAccounts = target
		s.totalStateBytes += delta * (int64(AccountStateOverhead) + s.pubKeySize)
	}
}

func (s *Simulator) snapshot() Sample {
	return Sample{
		TxCount:         s.txCount,
		TotalStateBytes: s.totalStateBytes,
		TotalTxBytes:    s.totalTxBytes,
		UniqueAccounts:  s.uniqueAccounts,
	}
}

func (s *Simulator) run(nSamples int) []Sample {
	points := logSamplePoints(s.numTx, nSamples)
	out := make([]Sample, 0, len(points)+1)
	out = append(out, Sample{}) // t=0 origin
	pIdx := 0
	for s.txCount < s.numTx {
		s.step()
		for pIdx < len(points) && s.txCount == points[pIdx] {
			out = append(out, s.snapshot())
			pIdx++
		}
	}
	return out
}

func main() {
	scheme := flag.String("scheme", "secp256k1", "signature scheme: secp256k1|mldsa44")
	numTx := flag.Int64("num-tx", 100000, "number of transactions to simulate")
	txMix := flag.String("tx-mix", "transfer:60,migration:20,stake:15,gov:5", "tx-type mix (type:pct,...)")
	output := flag.String("output", "results.json", "output JSON path")
	seed := flag.Int64("seed", 42, "RNG seed")
	alpha := flag.Float64("account-alpha", defaultAccountAlpha, "account-growth exponent (unique = K*n^alpha)")
	kCoef := flag.Float64("account-k", defaultAccountK, "account-growth coefficient K")
	samples := flag.Int("samples", 200, "approximate number of log-spaced series samples")
	flag.Parse()

	mix, names, err := parseMix(*txMix)
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(2)
	}
	sim, err := newSim(*scheme, *numTx, mix, names, *alpha, *kCoef, *seed)
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(2)
	}

	fmt.Fprintf(os.Stderr, "sim: scheme=%s num_tx=%d mix=%s\n", *scheme, *numTx, *txMix)
	series := sim.run(*samples)
	res := SimResult{
		Scheme:              *scheme,
		NumTx:               *numTx,
		TxMix:               *txMix,
		PubkeyBytes:         sim.pubKeySize,
		SignatureBytes:      sim.sigSize,
		EnvelopeOverhead:    TxEnvelopeOverhead,
		AccountOverhead:     AccountStateOverhead,
		FinalStateBytes:     sim.totalStateBytes,
		FinalTxBytes:        sim.totalTxBytes,
		FinalUniqueAccounts: sim.uniqueAccounts,
		Series:              series,
	}
	data, err := json.MarshalIndent(&res, "", "  ")
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
	if err := os.WriteFile(*output, data, 0644); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
	fmt.Fprintf(os.Stderr, "done: state=%d B tx_bytes=%d B accounts=%d -> %s\n",
		sim.totalStateBytes, sim.totalTxBytes, sim.uniqueAccounts, *output)
}
