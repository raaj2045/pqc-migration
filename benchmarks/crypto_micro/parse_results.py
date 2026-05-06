#!/usr/bin/env python3
"""Parse Go benchmark output and convert to JSON format."""

import re
import json
import statistics
from collections import defaultdict

def parse_benchmark_output(filepath):
    """Parse raw benchmark output file."""
    results = defaultdict(list)

    # Regex to match benchmark lines
    # Example: BenchmarkKeyGen_Secp256k1-12    2136270       544.7 ns/op      96 B/op       2 allocs/op
    bench_regex = re.compile(
        r'^Benchmark(\w+)-\d+\s+(\d+)\s+([\d.]+)\s+ns/op(?:\s+(\d+)\s+B/op)?(?:\s+(\d+)\s+allocs/op)?'
    )

    with open(filepath, 'r') as f:
        for line in f:
            match = bench_regex.match(line.strip())
            if match:
                name = match.group(1)
                ns_per_op = float(match.group(3))
                bytes_per_op = int(match.group(4)) if match.group(4) else 0
                allocs_per_op = int(match.group(5)) if match.group(5) else 0

                results[name].append({
                    'ns_per_op': ns_per_op,
                    'bytes_per_op': bytes_per_op,
                    'allocs_per_op': allocs_per_op,
                })

    return results

def parse_result_name(name):
    """Parse benchmark name to extract operation, scheme, and parameters."""
    result = {
        'operation': '',
        'scheme': '',
        'msg_size_bytes': 0,
        'batch_size': 0,
        'goroutines': 0,
    }

    # Determine operation
    if name.startswith('KeyGen_'):
        result['operation'] = 'KeyGen'
    elif name.startswith('Sign_'):
        result['operation'] = 'Sign'
    elif name.startswith('Verify_'):
        result['operation'] = 'Verify'
    elif name.startswith('BatchVerify_'):
        result['operation'] = 'BatchVerify'
    elif name.startswith('ConcurrentSign_'):
        result['operation'] = 'ConcurrentSign'
    elif name.startswith('Throughput_'):
        result['operation'] = 'Throughput'

    # Determine scheme
    if 'Secp256k1' in name:
        result['scheme'] = 'secp256k1'
    elif 'MLDSA44' in name:
        result['scheme'] = 'mldsa44'

    # Parse message size
    if '_100B' in name:
        result['msg_size_bytes'] = 100
    elif '_1KB' in name:
        result['msg_size_bytes'] = 1024
    elif '_10KB' in name:
        result['msg_size_bytes'] = 10240
    elif '_100KB' in name:
        result['msg_size_bytes'] = 102400

    # Parse batch size
    if name.startswith('BatchVerify_'):
        parts = name.split('_')
        if len(parts) >= 3:
            try:
                result['batch_size'] = int(parts[2])
                result['msg_size_bytes'] = 100  # Default for batch
            except ValueError:
                pass

    # Parse goroutine count
    if name.startswith('ConcurrentSign_') or name.startswith('Throughput_'):
        parts = name.split('_')
        if len(parts) >= 3:
            try:
                result['goroutines'] = int(parts[2])
                if result['msg_size_bytes'] == 0:
                    result['msg_size_bytes'] = 1024 if name.startswith('ConcurrentSign_') else 256
            except ValueError:
                pass

    return result

def aggregate_results(results):
    """Aggregate results and compute median values."""
    aggregated = []

    for name, samples in results.items():
        if not samples:
            continue

        parsed = parse_result_name(name)

        # Compute median of samples
        ns_values = [s['ns_per_op'] for s in samples]
        median_ns = statistics.median(ns_values)

        # Use the first sample's memory stats (they should all be the same)
        bytes_per_op = samples[0]['bytes_per_op']
        allocs_per_op = samples[0]['allocs_per_op']

        entry = {
            'operation': parsed['operation'],
            'scheme': parsed['scheme'],
            'msg_size_bytes': parsed['msg_size_bytes'],
            'ns_per_op': int(median_ns),
            'allocs_per_op': allocs_per_op,
            'bytes_per_op': bytes_per_op,
        }

        if parsed['batch_size'] > 0:
            entry['batch_size'] = parsed['batch_size']

        if parsed['goroutines'] > 0:
            entry['goroutines'] = parsed['goroutines']

        aggregated.append(entry)

    return aggregated

def main():
    import os
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_file = os.path.join(script_dir, 'raw_benchmark.txt')
    output_file = os.path.join(script_dir, 'results.json')

    print(f"Parsing {input_file}...")
    results = parse_benchmark_output(input_file)
    print(f"Found {len(results)} unique benchmarks")

    aggregated = aggregate_results(results)
    print(f"Aggregated into {len(aggregated)} results")

    with open(output_file, 'w') as f:
        json.dump(aggregated, f, indent=2)

    print(f"Results written to {output_file}")

    # Print summary
    print("\n=== Summary ===")
    for r in sorted(aggregated, key=lambda x: (x['operation'], x['scheme'])):
        op = r['operation']
        scheme = r['scheme']
        ns = r['ns_per_op']
        us = ns / 1000
        ms = ns / 1_000_000

        if ns >= 1_000_000:
            time_str = f"{ms:.2f} ms"
        elif ns >= 1000:
            time_str = f"{us:.2f} us"
        else:
            time_str = f"{ns} ns"

        extra = ""
        if r.get('msg_size_bytes'):
            sizes = {100: '100B', 1024: '1KB', 10240: '10KB', 102400: '100KB', 256: '256B'}
            extra += f" [{sizes.get(r['msg_size_bytes'], str(r['msg_size_bytes']))}]"
        if r.get('batch_size'):
            extra += f" [batch={r['batch_size']}]"
        if r.get('goroutines'):
            extra += f" [goroutines={r['goroutines']}]"

        print(f"  {op:20s} {scheme:12s}{extra:30s}: {time_str:>12s}")

if __name__ == '__main__':
    main()
