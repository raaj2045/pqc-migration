package crypto_micro

import (
	"crypto/rand"
	"fmt"
	"runtime"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/cloudflare/circl/sign/mldsa/mldsa44"
	"github.com/cosmos/cosmos-sdk/crypto/keys/mldsa"
	"github.com/cosmos/cosmos-sdk/crypto/keys/secp256k1"
)

// BenchmarkResult represents a single benchmark result for JSON output
type BenchmarkResult struct {
	Operation    string `json:"operation"`
	Scheme       string `json:"scheme"`
	MsgSizeBytes int    `json:"msg_size_bytes"`
	NsPerOp      int64  `json:"ns_per_op"`
	AllocsPerOp  int64  `json:"allocs_per_op"`
	BytesPerOp   int64  `json:"bytes_per_op"`
	BatchSize    int    `json:"batch_size,omitempty"`
	Goroutines   int    `json:"goroutines,omitempty"`
}

var messageSizes = []int{100, 1024, 10240, 102400} // 100B, 1KB, 10KB, 100KB
var batchSizes = []int{10, 100, 1000}
var goroutineCounts = []int{1, 4, 8, 16}

// generateMessage creates a random message of the specified size
func generateMessage(size int) []byte {
	msg := make([]byte, size)
	rand.Read(msg)
	return msg
}

// ==================== KeyGen Benchmarks ====================

func BenchmarkKeyGen_Secp256k1(b *testing.B) {
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = secp256k1.GenPrivKey()
	}
}

func BenchmarkKeyGen_MLDSA44(b *testing.B) {
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, err := mldsa.GenPrivKey()
		if err != nil {
			b.Fatal(err)
		}
	}
}

// ==================== Sign Benchmarks ====================

func BenchmarkSign_Secp256k1_100B(b *testing.B) {
	benchmarkSignSecp256k1(b, 100)
}

func BenchmarkSign_Secp256k1_1KB(b *testing.B) {
	benchmarkSignSecp256k1(b, 1024)
}

func BenchmarkSign_Secp256k1_10KB(b *testing.B) {
	benchmarkSignSecp256k1(b, 10240)
}

func BenchmarkSign_Secp256k1_100KB(b *testing.B) {
	benchmarkSignSecp256k1(b, 102400)
}

func benchmarkSignSecp256k1(b *testing.B, msgSize int) {
	privKey := secp256k1.GenPrivKey()
	msg := generateMessage(msgSize)

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, err := privKey.Sign(msg)
		if err != nil {
			b.Fatal(err)
		}
	}
}

func BenchmarkSign_MLDSA44_100B(b *testing.B) {
	benchmarkSignMLDSA44(b, 100)
}

func BenchmarkSign_MLDSA44_1KB(b *testing.B) {
	benchmarkSignMLDSA44(b, 1024)
}

func BenchmarkSign_MLDSA44_10KB(b *testing.B) {
	benchmarkSignMLDSA44(b, 10240)
}

func BenchmarkSign_MLDSA44_100KB(b *testing.B) {
	benchmarkSignMLDSA44(b, 102400)
}

func benchmarkSignMLDSA44(b *testing.B, msgSize int) {
	privKey, err := mldsa.GenPrivKey()
	if err != nil {
		b.Fatal(err)
	}
	msg := generateMessage(msgSize)

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, err := privKey.Sign(msg)
		if err != nil {
			b.Fatal(err)
		}
	}
}

// ==================== Verify Benchmarks ====================

func BenchmarkVerify_Secp256k1_100B(b *testing.B) {
	benchmarkVerifySecp256k1(b, 100)
}

func BenchmarkVerify_Secp256k1_1KB(b *testing.B) {
	benchmarkVerifySecp256k1(b, 1024)
}

func BenchmarkVerify_Secp256k1_10KB(b *testing.B) {
	benchmarkVerifySecp256k1(b, 10240)
}

func BenchmarkVerify_Secp256k1_100KB(b *testing.B) {
	benchmarkVerifySecp256k1(b, 102400)
}

func benchmarkVerifySecp256k1(b *testing.B, msgSize int) {
	privKey := secp256k1.GenPrivKey()
	pubKey := privKey.PubKey()
	msg := generateMessage(msgSize)
	sig, err := privKey.Sign(msg)
	if err != nil {
		b.Fatal(err)
	}

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if !pubKey.VerifySignature(msg, sig) {
			b.Fatal("verification failed")
		}
	}
}

func BenchmarkVerify_MLDSA44_100B(b *testing.B) {
	benchmarkVerifyMLDSA44(b, 100)
}

func BenchmarkVerify_MLDSA44_1KB(b *testing.B) {
	benchmarkVerifyMLDSA44(b, 1024)
}

func BenchmarkVerify_MLDSA44_10KB(b *testing.B) {
	benchmarkVerifyMLDSA44(b, 10240)
}

func BenchmarkVerify_MLDSA44_100KB(b *testing.B) {
	benchmarkVerifyMLDSA44(b, 102400)
}

func benchmarkVerifyMLDSA44(b *testing.B, msgSize int) {
	privKey, err := mldsa.GenPrivKey()
	if err != nil {
		b.Fatal(err)
	}
	pubKey := privKey.PubKey()
	msg := generateMessage(msgSize)
	sig, err := privKey.Sign(msg)
	if err != nil {
		b.Fatal(err)
	}

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if !pubKey.VerifySignature(msg, sig) {
			b.Fatal("verification failed")
		}
	}
}

// ==================== Batch Verify Benchmarks ====================
// Note: secp256k1 does not have native batch verification
// ML-DSA-44 via circl does not expose batch verification either
// We simulate batch by sequential verification and note this limitation

func BenchmarkBatchVerify_Secp256k1_10(b *testing.B) {
	benchmarkBatchVerifySecp256k1(b, 10)
}

func BenchmarkBatchVerify_Secp256k1_100(b *testing.B) {
	benchmarkBatchVerifySecp256k1(b, 100)
}

func BenchmarkBatchVerify_Secp256k1_1000(b *testing.B) {
	benchmarkBatchVerifySecp256k1(b, 1000)
}

func benchmarkBatchVerifySecp256k1(b *testing.B, batchSize int) {
	// Pre-generate keys, messages, and signatures
	type sigBundle struct {
		pubKey interface{ VerifySignature([]byte, []byte) bool }
		msg    []byte
		sig    []byte
	}

	bundles := make([]sigBundle, batchSize)
	for i := 0; i < batchSize; i++ {
		privKey := secp256k1.GenPrivKey()
		msg := generateMessage(100)
		sig, _ := privKey.Sign(msg)
		bundles[i] = sigBundle{privKey.PubKey(), msg, sig}
	}

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		for _, bundle := range bundles {
			if !bundle.pubKey.VerifySignature(bundle.msg, bundle.sig) {
				b.Fatal("verification failed")
			}
		}
	}
}

func BenchmarkBatchVerify_MLDSA44_10(b *testing.B) {
	benchmarkBatchVerifyMLDSA44(b, 10)
}

func BenchmarkBatchVerify_MLDSA44_100(b *testing.B) {
	benchmarkBatchVerifyMLDSA44(b, 100)
}

func BenchmarkBatchVerify_MLDSA44_1000(b *testing.B) {
	benchmarkBatchVerifyMLDSA44(b, 1000)
}

func benchmarkBatchVerifyMLDSA44(b *testing.B, batchSize int) {
	// Pre-generate keys, messages, and signatures
	type sigBundle struct {
		pubKey interface{ VerifySignature([]byte, []byte) bool }
		msg    []byte
		sig    []byte
	}

	bundles := make([]sigBundle, batchSize)
	for i := 0; i < batchSize; i++ {
		privKey, _ := mldsa.GenPrivKey()
		msg := generateMessage(100)
		sig, _ := privKey.Sign(msg)
		bundles[i] = sigBundle{privKey.PubKey(), msg, sig}
	}

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		for _, bundle := range bundles {
			if !bundle.pubKey.VerifySignature(bundle.msg, bundle.sig) {
				b.Fatal("verification failed")
			}
		}
	}
}

// ==================== Concurrent Signing Benchmarks ====================

func BenchmarkConcurrentSign_Secp256k1_1(b *testing.B) {
	benchmarkConcurrentSignSecp256k1(b, 1)
}

func BenchmarkConcurrentSign_Secp256k1_4(b *testing.B) {
	benchmarkConcurrentSignSecp256k1(b, 4)
}

func BenchmarkConcurrentSign_Secp256k1_8(b *testing.B) {
	benchmarkConcurrentSignSecp256k1(b, 8)
}

func BenchmarkConcurrentSign_Secp256k1_16(b *testing.B) {
	benchmarkConcurrentSignSecp256k1(b, 16)
}

func benchmarkConcurrentSignSecp256k1(b *testing.B, numGoroutines int) {
	// Pre-generate keys for each goroutine
	keys := make([]*secp256k1.PrivKey, numGoroutines)
	for i := 0; i < numGoroutines; i++ {
		keys[i] = secp256k1.GenPrivKey()
	}
	msg := generateMessage(1024) // 1KB message

	b.ReportAllocs()
	b.ResetTimer()

	for i := 0; i < b.N; i++ {
		var wg sync.WaitGroup
		opsPerGoroutine := 100 // Fixed operations per iteration
		var totalOps int64

		for g := 0; g < numGoroutines; g++ {
			wg.Add(1)
			go func(gIdx int) {
				defer wg.Done()
				for j := 0; j < opsPerGoroutine; j++ {
					_, err := keys[gIdx].Sign(msg)
					if err != nil {
						return
					}
					atomic.AddInt64(&totalOps, 1)
				}
			}(g)
		}
		wg.Wait()
	}
}

func BenchmarkConcurrentSign_MLDSA44_1(b *testing.B) {
	benchmarkConcurrentSignMLDSA44(b, 1)
}

func BenchmarkConcurrentSign_MLDSA44_4(b *testing.B) {
	benchmarkConcurrentSignMLDSA44(b, 4)
}

func BenchmarkConcurrentSign_MLDSA44_8(b *testing.B) {
	benchmarkConcurrentSignMLDSA44(b, 8)
}

func BenchmarkConcurrentSign_MLDSA44_16(b *testing.B) {
	benchmarkConcurrentSignMLDSA44(b, 16)
}

func benchmarkConcurrentSignMLDSA44(b *testing.B, numGoroutines int) {
	// Pre-generate keys for each goroutine
	keys := make([]*mldsa.PrivKey, numGoroutines)
	for i := 0; i < numGoroutines; i++ {
		key, err := mldsa.GenPrivKey()
		if err != nil {
			b.Fatal(err)
		}
		keys[i] = key
	}
	msg := generateMessage(1024) // 1KB message

	b.ReportAllocs()
	b.ResetTimer()

	for i := 0; i < b.N; i++ {
		var wg sync.WaitGroup
		opsPerGoroutine := 100 // Fixed operations per iteration
		var totalOps int64

		for g := 0; g < numGoroutines; g++ {
			wg.Add(1)
			go func(gIdx int) {
				defer wg.Done()
				for j := 0; j < opsPerGoroutine; j++ {
					_, err := keys[gIdx].Sign(msg)
					if err != nil {
						return
					}
					atomic.AddInt64(&totalOps, 1)
				}
			}(g)
		}
		wg.Wait()
	}
}

// ==================== Throughput Benchmarks ====================
// These measure total signatures per second with varying concurrency

func BenchmarkThroughput_Secp256k1_1(b *testing.B) {
	benchmarkThroughputSecp256k1(b, 1)
}

func BenchmarkThroughput_Secp256k1_4(b *testing.B) {
	benchmarkThroughputSecp256k1(b, 4)
}

func BenchmarkThroughput_Secp256k1_8(b *testing.B) {
	benchmarkThroughputSecp256k1(b, 8)
}

func BenchmarkThroughput_Secp256k1_16(b *testing.B) {
	benchmarkThroughputSecp256k1(b, 16)
}

func benchmarkThroughputSecp256k1(b *testing.B, numGoroutines int) {
	runtime.GOMAXPROCS(numGoroutines)

	// Pre-generate keys for each goroutine
	keys := make([]*secp256k1.PrivKey, numGoroutines)
	for i := 0; i < numGoroutines; i++ {
		keys[i] = secp256k1.GenPrivKey()
	}
	msg := generateMessage(256) // Typical transaction size

	b.ReportAllocs()
	b.ResetTimer()

	var wg sync.WaitGroup
	opsPerGoroutine := b.N / numGoroutines
	if opsPerGoroutine == 0 {
		opsPerGoroutine = 1
	}

	for g := 0; g < numGoroutines; g++ {
		wg.Add(1)
		go func(gIdx int) {
			defer wg.Done()
			for j := 0; j < opsPerGoroutine; j++ {
				keys[gIdx].Sign(msg)
			}
		}(g)
	}
	wg.Wait()
}

func BenchmarkThroughput_MLDSA44_1(b *testing.B) {
	benchmarkThroughputMLDSA44(b, 1)
}

func BenchmarkThroughput_MLDSA44_4(b *testing.B) {
	benchmarkThroughputMLDSA44(b, 4)
}

func BenchmarkThroughput_MLDSA44_8(b *testing.B) {
	benchmarkThroughputMLDSA44(b, 8)
}

func BenchmarkThroughput_MLDSA44_16(b *testing.B) {
	benchmarkThroughputMLDSA44(b, 16)
}

func benchmarkThroughputMLDSA44(b *testing.B, numGoroutines int) {
	runtime.GOMAXPROCS(numGoroutines)

	// Pre-generate keys for each goroutine
	keys := make([]*mldsa.PrivKey, numGoroutines)
	for i := 0; i < numGoroutines; i++ {
		key, err := mldsa.GenPrivKey()
		if err != nil {
			b.Fatal(err)
		}
		keys[i] = key
	}
	msg := generateMessage(256) // Typical transaction size

	b.ReportAllocs()
	b.ResetTimer()

	var wg sync.WaitGroup
	opsPerGoroutine := b.N / numGoroutines
	if opsPerGoroutine == 0 {
		opsPerGoroutine = 1
	}

	for g := 0; g < numGoroutines; g++ {
		wg.Add(1)
		go func(gIdx int) {
			defer wg.Done()
			for j := 0; j < opsPerGoroutine; j++ {
				keys[gIdx].Sign(msg)
			}
		}(g)
	}
	wg.Wait()
}

// ==================== Key and Signature Size Info ====================

func TestKeySizes(t *testing.T) {
	// secp256k1
	secpPriv := secp256k1.GenPrivKey()
	secpPub := secpPriv.PubKey()
	msg := []byte("test message")
	secpSig, _ := secpPriv.Sign(msg)

	// ML-DSA-44
	mldsaPriv, _ := mldsa.GenPrivKey()
	mldsaPub := mldsaPriv.PubKey()
	mldsaSig, _ := mldsaPriv.Sign(msg)

	fmt.Printf("\n=== Key and Signature Sizes ===\n")
	fmt.Printf("secp256k1:\n")
	fmt.Printf("  Private Key: %d bytes\n", len(secpPriv.Bytes()))
	fmt.Printf("  Public Key:  %d bytes\n", len(secpPub.Bytes()))
	fmt.Printf("  Signature:   %d bytes\n", len(secpSig))
	fmt.Printf("\nML-DSA-44:\n")
	fmt.Printf("  Private Key: %d bytes\n", len(mldsaPriv.Bytes()))
	fmt.Printf("  Public Key:  %d bytes\n", len(mldsaPub.Bytes()))
	fmt.Printf("  Signature:   %d bytes\n", len(mldsaSig))
	fmt.Printf("\nML-DSA-44 constants:\n")
	fmt.Printf("  Seed Size:       %d bytes\n", mldsa44.SeedSize)
	fmt.Printf("  Public Key Size: %d bytes\n", mldsa44.PublicKeySize)
	fmt.Printf("  Private Key Size: %d bytes\n", mldsa44.PrivateKeySize)
	fmt.Printf("  Signature Size:  %d bytes\n", mldsa44.SignatureSize)
}

// ==================== JSON Export Helper ====================

// This function is not a benchmark but a helper to export results
// Run with: go test -v -run TestExportResults
func TestExportResults(t *testing.T) {
	t.Skip("Run benchmarks separately with: go test -bench=. -benchmem -count=5 -json > raw_results.json")
}
