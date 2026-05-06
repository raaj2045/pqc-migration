package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
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

func main() {
	fmt.Println("Running crypto benchmarks...")

	// Run benchmarks with multiple iterations for statistical significance
	cmd := exec.Command("go", "test", "-bench=.", "-benchmem", "-count=10", "-timeout=30m")
	cmd.Dir = ".."

	output, err := cmd.CombinedOutput()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error running benchmarks: %v\nOutput: %s\n", err, output)
		os.Exit(1)
	}

	// Save raw output for debugging
	os.WriteFile("../raw_benchmark.txt", output, 0644)

	// Parse results
	results := parseBenchmarkOutput(string(output))

	// Write JSON results
	jsonData, err := json.MarshalIndent(results, "", "  ")
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error marshaling JSON: %v\n", err)
		os.Exit(1)
	}

	if err := os.WriteFile("../results.json", jsonData, 0644); err != nil {
		fmt.Fprintf(os.Stderr, "Error writing results.json: %v\n", err)
		os.Exit(1)
	}

	fmt.Printf("Benchmarks complete. %d results written to results.json\n", len(results))
}

func parseBenchmarkOutput(output string) []BenchmarkResult {
	var results []BenchmarkResult

	// Regex to match benchmark lines
	// Example: BenchmarkKeyGen_Secp256k1-8    	   50000	     25000 ns/op	    1234 B/op	      12 allocs/op
	benchRegex := regexp.MustCompile(`^Benchmark(\w+)-\d+\s+(\d+)\s+(\d+(?:\.\d+)?)\s+ns/op(?:\s+(\d+)\s+B/op)?(?:\s+(\d+)\s+allocs/op)?`)

	// Map to aggregate results across runs (for -count=N)
	aggregated := make(map[string]*aggregatedResult)

	scanner := bufio.NewScanner(strings.NewReader(output))
	for scanner.Scan() {
		line := scanner.Text()
		matches := benchRegex.FindStringSubmatch(line)
		if matches == nil {
			continue
		}

		name := matches[1]
		nsPerOp, _ := strconv.ParseFloat(matches[3], 64)
		bytesPerOp := int64(0)
		allocsPerOp := int64(0)
		if matches[4] != "" {
			bytesPerOp, _ = strconv.ParseInt(matches[4], 10, 64)
		}
		if matches[5] != "" {
			allocsPerOp, _ = strconv.ParseInt(matches[5], 10, 64)
		}

		if _, ok := aggregated[name]; !ok {
			aggregated[name] = &aggregatedResult{
				samples: make([]float64, 0),
			}
		}
		aggregated[name].samples = append(aggregated[name].samples, nsPerOp)
		aggregated[name].bytesPerOp = bytesPerOp
		aggregated[name].allocsPerOp = allocsPerOp
	}

	// Convert aggregated results to final results
	for name, agg := range aggregated {
		result := parseResultName(name)
		result.NsPerOp = int64(median(agg.samples))
		result.BytesPerOp = agg.bytesPerOp
		result.AllocsPerOp = agg.allocsPerOp
		results = append(results, result)
	}

	return results
}

type aggregatedResult struct {
	samples     []float64
	bytesPerOp  int64
	allocsPerOp int64
}

func median(samples []float64) float64 {
	if len(samples) == 0 {
		return 0
	}
	// Simple median calculation
	n := len(samples)
	// Sort samples
	for i := 0; i < n-1; i++ {
		for j := i + 1; j < n; j++ {
			if samples[i] > samples[j] {
				samples[i], samples[j] = samples[j], samples[i]
			}
		}
	}
	if n%2 == 0 {
		return (samples[n/2-1] + samples[n/2]) / 2
	}
	return samples[n/2]
}

func parseResultName(name string) BenchmarkResult {
	result := BenchmarkResult{}

	// Determine operation and scheme
	if strings.HasPrefix(name, "KeyGen_") {
		result.Operation = "KeyGen"
		result.MsgSizeBytes = 0
	} else if strings.HasPrefix(name, "Sign_") {
		result.Operation = "Sign"
	} else if strings.HasPrefix(name, "Verify_") {
		result.Operation = "Verify"
	} else if strings.HasPrefix(name, "BatchVerify_") {
		result.Operation = "BatchVerify"
	} else if strings.HasPrefix(name, "ConcurrentSign_") {
		result.Operation = "ConcurrentSign"
	} else if strings.HasPrefix(name, "Throughput_") {
		result.Operation = "Throughput"
	}

	// Determine scheme
	if strings.Contains(name, "Secp256k1") {
		result.Scheme = "secp256k1"
	} else if strings.Contains(name, "MLDSA44") {
		result.Scheme = "mldsa44"
	}

	// Parse message size
	if strings.Contains(name, "_100B") {
		result.MsgSizeBytes = 100
	} else if strings.Contains(name, "_1KB") {
		result.MsgSizeBytes = 1024
	} else if strings.Contains(name, "_10KB") {
		result.MsgSizeBytes = 10240
	} else if strings.Contains(name, "_100KB") {
		result.MsgSizeBytes = 102400
	} else if strings.Contains(name, "_256") {
		result.MsgSizeBytes = 256
	}

	// Parse batch size
	if strings.HasPrefix(name, "BatchVerify_") {
		parts := strings.Split(name, "_")
		if len(parts) >= 3 {
			batch, _ := strconv.Atoi(parts[2])
			result.BatchSize = batch
			result.MsgSizeBytes = 100 // Default for batch
		}
	}

	// Parse goroutine count
	if strings.HasPrefix(name, "ConcurrentSign_") || strings.HasPrefix(name, "Throughput_") {
		parts := strings.Split(name, "_")
		if len(parts) >= 3 {
			goroutines, _ := strconv.Atoi(parts[2])
			result.Goroutines = goroutines
			if result.MsgSizeBytes == 0 {
				if strings.HasPrefix(name, "Throughput_") {
					result.MsgSizeBytes = 256
				} else {
					result.MsgSizeBytes = 1024
				}
			}
		}
	}

	return result
}
