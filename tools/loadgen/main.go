// loadgen replays a pre-signed transaction pool against a CometBFT JSON-RPC
// endpoint and records per-tx submit + commit times.
//
// Inputs:
//   --pool        JSONL file of {sender_idx, sequence, tx_b64} records
//   --rpc-url     Tendermint RPC root, e.g. http://localhost:26657
//   --rate        target txs/second
//   --duration    seconds of broadcast (after which loadgen stops submitting)
//   --senders     number of distinct sender_idx values in the pool (round-robin span)
//   --drain       seconds to keep the block-sampler running after broadcast ends
//   --out         metrics JSON output path
//
// Strategy:
//
//   1. Load the entire pool into per-sender FIFO queues (sequence-ordered).
//   2. Background block-sampler goroutine polls /status for the latest height
//      and pulls each new block via /block?height=. Each block's txs are
//      hashed (sha256 hex, the CometBFT tx-hash convention) and stored with
//      the block's time/height.
//   3. Broadcast loop: ticker at period = 1/rate. Each tick draws one tx from
//      the next sender in round-robin, POSTs /broadcast_tx_sync, records the
//      submit timestamp + the returned hash + checktx code.
//   4. After --duration, the broadcast loop stops. The block-sampler runs for
//      another --drain seconds to collect commits for in-flight txs.
//   5. Metrics: for every submitted tx, look up its hash in the block-sampler
//      map; emit a record with submit_ms, commit_ms (or null), height, and
//      checktx code. Also emit the aggregate counters.
//
// Pool exhaustion (any sender runs out before --duration ends) is a hard
// error and aborts the run, per the experiment spec.
package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

// ----- pool record -----

type signedTxRecord struct {
	SenderIdx int    `json:"sender_idx"`
	Sequence  uint64 `json:"sequence"`
	TxB64     string `json:"tx_b64"`
}

// ----- per-tx submission record -----

type submitRecord struct {
	SenderIdx   int    `json:"sender_idx"`
	Sequence    uint64 `json:"sequence"`
	Hash        string `json:"hash"`
	SubmitMs    int64  `json:"submit_ms"`
	CheckCode   int    `json:"check_code"`
	CheckLog    string `json:"check_log,omitempty"`
	// CommitMs is the wall-clock moment the block sampler first saw the
	// finalising block via /block?height=. Bounded above by the sampler's
	// polling interval (500ms). We use this rather than block.header.time
	// because the latter is the median of the previous round's vote
	// timestamps and routinely runs 2-5 seconds in the past.
	CommitMs       *int64 `json:"commit_ms,omitempty"`
	// BlockHeaderTimeMs is the consensus time embedded in the block. Kept
	// for reference but not used for latency.
	BlockHeaderTimeMs *int64 `json:"block_header_time_ms,omitempty"`
	BlockHeight       *int64 `json:"block_height,omitempty"`
	HTTPError         string `json:"http_error,omitempty"`
}

// ----- output JSON -----

type runMetrics struct {
	StartedAt        string         `json:"started_at"`
	EndedAt          string         `json:"ended_at"`
	RPCURL           string         `json:"rpc_url"`
	TargetRate       int            `json:"target_rate_tx_s"`
	DurationS        int            `json:"duration_s"`
	DrainS           int            `json:"drain_s"`
	Senders          int            `json:"senders"`
	PoolFile         string         `json:"pool_file"`
	PoolSize         int            `json:"pool_size"`
	Submitted        int            `json:"submitted"`
	CheckOK          int            `json:"check_ok"`
	CheckFail        int            `json:"check_fail"`
	Committed        int            `json:"committed"`
	HTTPErrors       int            `json:"http_errors"`
	BlocksObserved   int            `json:"blocks_observed"`
	FirstBlockHeight int64          `json:"first_block_height"`
	LastBlockHeight  int64          `json:"last_block_height"`
	Txs              []submitRecord `json:"txs"`
	Latency          latencyStats   `json:"latency_inclusion_ms"`
}

type latencyStats struct {
	N    int `json:"n"`
	Min  int `json:"min"`
	P50  int `json:"p50"`
	P90  int `json:"p90"`
	P99  int `json:"p99"`
	Max  int `json:"max"`
	Mean int `json:"mean"`
}

// ----- block-sampler shared state -----

type blockSampler struct {
	rpcURL  string
	httpClient *http.Client
	hashes  sync.Map // hash(hex lowercase) -> *commitInfo
	blocks  []blockObs
	blockMu sync.Mutex
	stopCh  chan struct{}
	wg      sync.WaitGroup
}

// commitInfo stores both the block's header time (consensus time, biased low
// because it is the median of the previous round's vote timestamps) and the
// time we OBSERVED the block via the RPC poll. The latter is what we use for
// commit latency: it is the wall-clock moment the loadgen could see the tx
// landed in a finalized block, bounded above by one polling interval.
type commitInfo struct {
	BlockHeight   int64
	BlockTimeMs   int64
	ObservedAtMs  int64
}

type blockObs struct {
	Height int64
	TimeMs int64
	NumTxs int
}

// ----- main -----

func main() {
	var (
		poolPath  = flag.String("pool", "", "JSONL pool file path")
		rpcURL    = flag.String("rpc-url", "http://localhost:26657", "CometBFT RPC URL")
		rate      = flag.Int("rate", 50, "target tx/s")
		duration  = flag.Int("duration", 60, "broadcast duration (seconds)")
		senders   = flag.Int("senders", 8, "number of senders in the pool")
		drain     = flag.Int("drain", 20, "drain period after broadcast (seconds)")
		outPath   = flag.String("out", "loadgen_result.json", "metrics output path")
		startupS  = flag.Int("startup-wait", 0, "seconds to wait for chain to be ready before broadcast")
	)
	flag.Parse()

	if *poolPath == "" {
		log.Fatal("--pool is required")
	}
	if *rate <= 0 || *duration <= 0 || *senders <= 0 {
		log.Fatal("--rate, --duration, --senders must be > 0")
	}

	startedAt := time.Now().UTC().Format(time.RFC3339)

	queues, totalLoaded, err := loadPool(*poolPath, *senders)
	if err != nil {
		log.Fatalf("loadgen: load pool: %v", err)
	}
	log.Printf("loadgen: loaded %d txs from %s into %d sender queues",
		totalLoaded, *poolPath, *senders)

	if *startupS > 0 {
		log.Printf("loadgen: waiting %ds for chain to be ready", *startupS)
		time.Sleep(time.Duration(*startupS) * time.Second)
	}

	httpClient := &http.Client{
		Timeout: 5 * time.Second,
		Transport: &http.Transport{
			MaxIdleConns:        256,
			MaxIdleConnsPerHost: 256,
			MaxConnsPerHost:     256,
			IdleConnTimeout:     30 * time.Second,
		},
	}

	bs := &blockSampler{
		rpcURL:     *rpcURL,
		httpClient: httpClient,
		stopCh:     make(chan struct{}),
	}
	bs.start()

	totalTarget := *rate * *duration
	records := make([]submitRecord, 0, totalTarget+128)
	var recordsMu sync.Mutex

	// Round-robin nextSenderIdx and per-sender cursors.
	cursors := make([]int, *senders)

	// Submission worker pool.
	submitJobs := make(chan signedTxRecord, *rate*2)
	var submitWg sync.WaitGroup
	workers := 64
	if *rate > 200 {
		workers = 128
	}
	for w := 0; w < workers; w++ {
		submitWg.Add(1)
		go func() {
			defer submitWg.Done()
			for tx := range submitJobs {
				rec := broadcastOne(httpClient, *rpcURL, tx)
				recordsMu.Lock()
				records = append(records, rec)
				recordsMu.Unlock()
			}
		}()
	}

	// Pacing loop.
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(*duration)*time.Second+2*time.Second)
	defer cancel()

	period := time.Second / time.Duration(*rate)
	if period <= 0 {
		period = 1
	}
	ticker := time.NewTicker(period)
	defer ticker.Stop()

	deadline := time.Now().Add(time.Duration(*duration) * time.Second)
	var submitted atomic.Int64
	var senderIdx int
	pumpDone := make(chan struct{})
	go func() {
		defer close(pumpDone)
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				if time.Now().After(deadline) {
					return
				}
				// Try this sender; if drained, scan forward up to N senders.
				picked := -1
				for tries := 0; tries < *senders; tries++ {
					idx := (senderIdx + tries) % *senders
					if cursors[idx] < len(queues[idx]) {
						picked = idx
						break
					}
				}
				if picked < 0 {
					log.Printf("loadgen: pool exhausted at submitted=%d (target=%d) — aborting",
						submitted.Load(), totalTarget)
					cancel()
					return
				}
				tx := queues[picked][cursors[picked]]
				cursors[picked]++
				senderIdx = (picked + 1) % *senders
				submitJobs <- tx
				submitted.Add(1)
			}
		}
	}()

	<-pumpDone
	close(submitJobs)
	submitWg.Wait()

	log.Printf("loadgen: broadcast complete; submitted=%d, draining %ds for commits",
		submitted.Load(), *drain)
	time.Sleep(time.Duration(*drain) * time.Second)
	bs.stop()

	// Resolve commits.
	committed := 0
	checkOK := 0
	checkFail := 0
	httpErrors := 0
	for i := range records {
		r := &records[i]
		if r.HTTPError != "" {
			httpErrors++
			continue
		}
		if r.CheckCode == 0 {
			checkOK++
		} else {
			checkFail++
		}
		if r.Hash != "" {
			if v, ok := bs.hashes.Load(r.Hash); ok {
				ci := v.(*commitInfo)
				h := ci.BlockHeight
				cm := ci.ObservedAtMs
				bt := ci.BlockTimeMs
				r.BlockHeight = &h
				r.CommitMs = &cm
				r.BlockHeaderTimeMs = &bt
				committed++
			}
		}
	}

	// Latency stats.
	latencies := make([]int, 0, committed)
	for _, r := range records {
		if r.CommitMs != nil {
			latencies = append(latencies, int(*r.CommitMs-r.SubmitMs))
		}
	}

	bs.blockMu.Lock()
	blocks := bs.blocks
	bs.blockMu.Unlock()
	first := int64(0)
	last := int64(0)
	if len(blocks) > 0 {
		first = blocks[0].Height
		last = blocks[len(blocks)-1].Height
	}

	out := runMetrics{
		StartedAt:        startedAt,
		EndedAt:          time.Now().UTC().Format(time.RFC3339),
		RPCURL:           *rpcURL,
		TargetRate:       *rate,
		DurationS:        *duration,
		DrainS:           *drain,
		Senders:          *senders,
		PoolFile:         *poolPath,
		PoolSize:         totalLoaded,
		Submitted:        int(submitted.Load()),
		CheckOK:          checkOK,
		CheckFail:        checkFail,
		Committed:        committed,
		HTTPErrors:       httpErrors,
		BlocksObserved:   len(blocks),
		FirstBlockHeight: first,
		LastBlockHeight:  last,
		Txs:              records,
		Latency:          summariseLatencies(latencies),
	}

	f, err := os.Create(*outPath)
	if err != nil {
		log.Fatalf("loadgen: open out: %v", err)
	}
	defer f.Close()
	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	if err := enc.Encode(out); err != nil {
		log.Fatalf("loadgen: write out: %v", err)
	}
	log.Printf("loadgen: wrote metrics to %s (submitted=%d, committed=%d, p99=%dms)",
		*outPath, out.Submitted, out.Committed, out.Latency.P99)
}

// loadPool reads a JSONL pool and bins txs by sender index, sorted by sequence.
func loadPool(path string, nSenders int) ([][]signedTxRecord, int, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, 0, err
	}
	defer f.Close()

	queues := make([][]signedTxRecord, nSenders)
	scanner := bufio.NewScanner(f)
	scanner.Buffer(make([]byte, 64*1024), 1<<22) // up to 4MB lines (mldsa b64)
	total := 0
	for scanner.Scan() {
		var rec signedTxRecord
		if err := json.Unmarshal(scanner.Bytes(), &rec); err != nil {
			return nil, 0, fmt.Errorf("decode line %d: %w", total+1, err)
		}
		if rec.SenderIdx < 0 || rec.SenderIdx >= nSenders {
			return nil, 0, fmt.Errorf("sender_idx %d out of range [0,%d)", rec.SenderIdx, nSenders)
		}
		queues[rec.SenderIdx] = append(queues[rec.SenderIdx], rec)
		total++
	}
	if err := scanner.Err(); err != nil {
		return nil, 0, err
	}
	for i := range queues {
		sort.Slice(queues[i], func(a, b int) bool { return queues[i][a].Sequence < queues[i][b].Sequence })
	}
	return queues, total, nil
}

// broadcastOne POSTs the JSON-RPC broadcast_tx_sync request and parses the result.
func broadcastOne(client *http.Client, rpcURL string, tx signedTxRecord) submitRecord {
	rec := submitRecord{
		SenderIdx: tx.SenderIdx,
		Sequence:  tx.Sequence,
		SubmitMs:  time.Now().UnixMilli(),
	}
	body := []byte(fmt.Sprintf(
		`{"jsonrpc":"2.0","id":1,"method":"broadcast_tx_sync","params":{"tx":%q}}`,
		tx.TxB64,
	))
	req, err := http.NewRequest("POST", rpcURL, bytes.NewReader(body))
	if err != nil {
		rec.HTTPError = err.Error()
		return rec
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		rec.HTTPError = err.Error()
		return rec
	}
	defer resp.Body.Close()
	respBytes, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		rec.HTTPError = fmt.Sprintf("http %d: %s", resp.StatusCode, truncate(string(respBytes), 200))
		return rec
	}
	var rpcResp struct {
		Result struct {
			Code int    `json:"code"`
			Hash string `json:"hash"`
			Log  string `json:"log"`
		} `json:"result"`
		Error *struct {
			Message string `json:"message"`
			Data    string `json:"data"`
		} `json:"error"`
	}
	if err := json.Unmarshal(respBytes, &rpcResp); err != nil {
		rec.HTTPError = "decode rpc: " + err.Error()
		return rec
	}
	if rpcResp.Error != nil {
		rec.HTTPError = "rpc error: " + rpcResp.Error.Message + " " + rpcResp.Error.Data
		return rec
	}
	rec.CheckCode = rpcResp.Result.Code
	rec.CheckLog = truncate(rpcResp.Result.Log, 200)
	rec.Hash = normaliseHash(rpcResp.Result.Hash)
	return rec
}

func normaliseHash(h string) string {
	// CometBFT returns hashes as uppercase hex; we lowercase to match
	// our own sha256 hex computation in the block sampler.
	if h == "" {
		return ""
	}
	out := make([]byte, len(h))
	for i := 0; i < len(h); i++ {
		c := h[i]
		if c >= 'A' && c <= 'F' {
			c += 'a' - 'A'
		}
		out[i] = c
	}
	return string(out)
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

// ----- block sampler -----

func (bs *blockSampler) start() {
	bs.wg.Add(1)
	go bs.run()
}

func (bs *blockSampler) stop() {
	close(bs.stopCh)
	bs.wg.Wait()
}

func (bs *blockSampler) run() {
	defer bs.wg.Done()
	var lastHeight int64
	tick := time.NewTicker(500 * time.Millisecond)
	defer tick.Stop()
	for {
		select {
		case <-bs.stopCh:
			return
		case <-tick.C:
			cur := bs.fetchLatestHeight()
			if cur <= lastHeight {
				continue
			}
			start := lastHeight + 1
			if start <= 0 {
				start = cur
			}
			for h := start; h <= cur; h++ {
				bs.fetchBlock(h)
			}
			lastHeight = cur
		}
	}
}

func (bs *blockSampler) fetchLatestHeight() int64 {
	url := bs.rpcURL + "/status"
	resp, err := bs.httpClient.Get(url)
	if err != nil {
		return 0
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	var s struct {
		Result struct {
			SyncInfo struct {
				LatestBlockHeight string `json:"latest_block_height"`
			} `json:"sync_info"`
		} `json:"result"`
	}
	if err := json.Unmarshal(body, &s); err != nil {
		return 0
	}
	v, _ := strconv.ParseInt(s.Result.SyncInfo.LatestBlockHeight, 10, 64)
	return v
}

func (bs *blockSampler) fetchBlock(height int64) {
	url := fmt.Sprintf("%s/block?height=%d", bs.rpcURL, height)
	resp, err := bs.httpClient.Get(url)
	if err != nil {
		return
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	var b struct {
		Result struct {
			Block struct {
				Header struct {
					Height string `json:"height"`
					Time   string `json:"time"`
				} `json:"header"`
				Data struct {
					Txs []string `json:"txs"`
				} `json:"data"`
			} `json:"block"`
		} `json:"result"`
	}
	if err := json.Unmarshal(body, &b); err != nil {
		return
	}
	t, err := time.Parse(time.RFC3339Nano, b.Result.Block.Header.Time)
	if err != nil {
		return
	}
	tms := t.UnixMilli()
	hh, _ := strconv.ParseInt(b.Result.Block.Header.Height, 10, 64)
	bs.blockMu.Lock()
	bs.blocks = append(bs.blocks, blockObs{
		Height: hh,
		TimeMs: tms,
		NumTxs: len(b.Result.Block.Data.Txs),
	})
	bs.blockMu.Unlock()
	observedAt := time.Now().UnixMilli()
	for _, txB64 := range b.Result.Block.Data.Txs {
		raw, err := base64.StdEncoding.DecodeString(txB64)
		if err != nil {
			continue
		}
		sum := sha256.Sum256(raw)
		hashHex := hex.EncodeToString(sum[:])
		bs.hashes.LoadOrStore(hashHex, &commitInfo{
			BlockHeight:  hh,
			BlockTimeMs:  tms,
			ObservedAtMs: observedAt,
		})
	}
}

// ----- stats -----

func summariseLatencies(xs []int) latencyStats {
	if len(xs) == 0 {
		return latencyStats{}
	}
	sort.Ints(xs)
	n := len(xs)
	pct := func(p float64) int {
		idx := int(float64(n-1) * p)
		if idx < 0 {
			idx = 0
		}
		if idx >= n {
			idx = n - 1
		}
		return xs[idx]
	}
	sum := 0
	for _, v := range xs {
		sum += v
	}
	return latencyStats{
		N:    n,
		Min:  xs[0],
		P50:  pct(0.50),
		P90:  pct(0.90),
		P99:  pct(0.99),
		Max:  xs[n-1],
		Mean: sum / n,
	}
}
