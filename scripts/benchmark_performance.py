"""
Performance benchmarking script for ALM/TVOG model.

Benchmarks:
1. Assumption lookup performance (cached vs uncached)
2. Policy projection throughput
3. Distributed processing scalability
4. Memory usage by portfolio size
5. Resource monitoring overhead
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import argparse
import json
import time
from datetime import datetime

import numpy as np
import pandas as pd
from par_model_v2.assumptions.flexible_provider import FlexibleAssumptionProvider
from par_model_v2.utils.memory_profiler import MemoryTracker
from par_model_v2.utils.resource_monitor import ResourceMonitor
from par_model_v2.valuation.asset_share_engine import AssetShareConfig, AssetShareEngine
from par_model_v2.valuation.distributed_executor import DistributedConfig, DistributedExecutor


def benchmark_assumption_lookups(assumptions_dir: str, n_iterations: int = 1000):
    """Benchmark assumption lookup performance."""
    print("\n" + "=" * 70)
    print("BENCHMARK: Assumption Lookups")
    print("=" * 70)

    provider = FlexibleAssumptionProvider(assumptions_dir)

    # Warm up
    for _ in range(10):
        provider.get_mortality("WL", "M", 35, "N", 1)

    # Benchmark uncached
    print(f"\nTesting {n_iterations} uncached lookups...")
    start = time.time()
    for _ in range(n_iterations):
        provider.clear_cache()
        provider.get_mortality("WL", "M", 35, "N", 1)
    uncached_time = time.time() - start
    uncached_per_lookup = (uncached_time / n_iterations) * 1000

    # Benchmark cached
    print(f"Testing {n_iterations} cached lookups...")
    start = time.time()
    for _ in range(n_iterations):
        provider.get_mortality("WL", "M", 35, "N", 1)
    cached_time = time.time() - start
    cached_per_lookup = (cached_time / n_iterations) * 1000

    speedup = uncached_time / cached_time

    print("\nResults:")
    print(f"  Uncached: {uncached_per_lookup:.3f}ms per lookup")
    print(f"  Cached:   {cached_per_lookup:.3f}ms per lookup")
    print(f"  Speedup:  {speedup:.1f}x")
    print(f"  Cache efficiency: {(1 - cached_time / uncached_time) * 100:.1f}%")

    return {"uncached_ms": uncached_per_lookup, "cached_ms": cached_per_lookup, "speedup": speedup}


def benchmark_policy_projection(
    assumptions_dir: str, n_policies: int = 100, n_timesteps: int = 360
):
    """Benchmark policy projection performance."""
    print("\n" + "=" * 70)
    print(f"BENCHMARK: Policy Projection ({n_policies} policies, {n_timesteps} timesteps)")
    print("=" * 70)

    # Setup
    provider = FlexibleAssumptionProvider(assumptions_dir)
    engine = AssetShareEngine(AssetShareConfig())

    # Generate sample policy
    np.random.seed(42)
    policy = pd.Series(
        {
            "policy_id": "POL000001",
            "product": "WL",
            "gender": "M",
            "age": 35,
            "smoker_status": "N",
            "sum_assured": 500000,
            "annual_premium": 10000,
            "premium_term": 20,
            "maturity_term": n_timesteps,
            "initial_asset_share": 0.0,
        }
    )

    # Generate returns
    returns = pd.Series(np.random.normal(0.06 / 12, 0.02 / 12, n_timesteps))

    # Get assumptions
    mortality_rates = pd.Series(
        [
            provider.get_mortality("WL", "M", 35 + t // 12, "N", t // 12 + 1) / 12
            for t in range(n_timesteps)
        ]
    )
    lapse_rates = pd.Series([0.05 / 12] * n_timesteps)
    expenses = pd.Series([50 / 12] * n_timesteps)

    # Benchmark
    print(f"\nProjecting {n_policies} policies...")
    with MemoryTracker("Policy Projection", log_results=False) as tracker:
        start = time.time()
        for _ in range(n_policies):
            engine.project_policy(
                policy=policy,
                investment_returns=returns,
                mortality_rates=mortality_rates,
                lapse_rates=lapse_rates,
                expenses=expenses,
                n_timesteps=n_timesteps,
            )
        duration = time.time() - start

    policies_per_sec = n_policies / duration
    ms_per_policy = (duration / n_policies) * 1000
    memory_per_policy = tracker.peak_mb / n_policies

    print("\nResults:")
    print(f"  Total duration: {duration:.2f}s")
    print(f"  Throughput: {policies_per_sec:.1f} policies/sec")
    print(f"  Time per policy: {ms_per_policy:.2f}ms")
    print(f"  Peak memory: {tracker.peak_mb:.2f} MB")
    print(f"  Memory per policy: {memory_per_policy:.4f} MB")

    return {
        "policies_per_sec": policies_per_sec,
        "ms_per_policy": ms_per_policy,
        "peak_memory_mb": tracker.peak_mb,
        "memory_per_policy_mb": memory_per_policy,
    }


def benchmark_distributed_processing(portfolio_sizes: list = [1000, 5000, 10000]):
    """Benchmark distributed processing scalability."""
    print("\n" + "=" * 70)
    print("BENCHMARK: Distributed Processing Scalability")
    print("=" * 70)

    results = []

    for n_policies in portfolio_sizes:
        print(f"\n--- Testing {n_policies:,} policies ---")

        # Generate portfolio
        np.random.seed(42)
        policies = pd.DataFrame(
            {
                "policy_id": [f"POL{i:06d}" for i in range(n_policies)],
                "value": np.random.randn(n_policies),
            }
        )

        # Configure executor
        config = DistributedConfig(chunk_size_auto=True, max_workers=4, verbose=False)
        executor = DistributedExecutor(config)

        # Simple processing function
        def process_chunk(chunk_df):
            # Simulate some work
            time.sleep(0.001)  # 1ms per chunk
            return [
                {"policy_id": row["policy_id"], "result": row["value"] * 2}
                for _, row in chunk_df.iterrows()
            ]

        # Execute and measure
        with MemoryTracker(f"Distributed {n_policies}", log_results=False) as tracker:
            start = time.time()
            result = executor.execute(
                data=policies, process_func=process_chunk, avg_item_memory_mb=0.5, resume=False
            )
            duration = time.time() - start

        throughput = n_policies / duration
        memory_per_policy = tracker.peak_mb / n_policies

        print(f"  Duration: {duration:.2f}s")
        print(f"  Throughput: {throughput:.1f} policies/sec")
        print(f"  Chunks: {result.chunks_completed}")
        print(f"  Peak memory: {tracker.peak_mb:.2f} MB")
        print(f"  Memory per policy: {memory_per_policy:.4f} MB")

        results.append(
            {
                "n_policies": n_policies,
                "duration_sec": duration,
                "throughput": throughput,
                "chunks": result.chunks_completed,
                "peak_memory_mb": tracker.peak_mb,
                "memory_per_policy_mb": memory_per_policy,
            }
        )

    return results


def benchmark_resource_monitoring(n_iterations: int = 1000):
    """Benchmark resource monitoring overhead."""
    print("\n" + "=" * 70)
    print("BENCHMARK: Resource Monitoring Overhead")
    print("=" * 70)

    monitor = ResourceMonitor()

    # Benchmark snapshot collection
    print(f"\nTesting {n_iterations} snapshots...")
    start = time.time()
    for _ in range(n_iterations):
        monitor.get_snapshot()
    duration = time.time() - start

    ms_per_snapshot = (duration / n_iterations) * 1000

    print("\nResults:")
    print(f"  Time per snapshot: {ms_per_snapshot:.3f}ms")
    print(f"  Snapshots per second: {n_iterations / duration:.1f}")
    print(f"  Total overhead: {duration * 1000:.2f}ms for {n_iterations} snapshots")

    # Benchmark chunk size calculation
    print("\nTesting chunk size calculation...")
    start = time.time()
    for _ in range(100):
        monitor.calculate_optimal_chunk_size(total_items=10000, avg_item_memory_mb=5.0)
    calc_duration = time.time() - start
    ms_per_calc = (calc_duration / 100) * 1000

    print(f"  Time per calculation: {ms_per_calc:.3f}ms")

    return {
        "snapshot_ms": ms_per_snapshot,
        "chunk_calc_ms": ms_per_calc,
        "overhead_pct": (ms_per_snapshot / 1000) * 100,  # Assuming 1s baseline
    }


def generate_benchmark_report(results: dict, output_file: str = None):
    """Generate comprehensive benchmark report."""
    report = []
    report.append("=" * 70)
    report.append("ALM/TVOG MODEL - PERFORMANCE BENCHMARK REPORT")
    report.append("=" * 70)
    report.append(f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append("\nSystem Information:")

    monitor = ResourceMonitor()
    snapshot = monitor.get_snapshot()
    report.append(f"  RAM Total: {snapshot.ram_total_gb:.2f} GB")
    report.append(f"  CPU Cores: {snapshot.cpu_count}")

    # Assumption lookups
    if "assumption_lookups" in results:
        al = results["assumption_lookups"]
        report.append("\n" + "-" * 70)
        report.append("1. ASSUMPTION LOOKUPS")
        report.append("-" * 70)
        report.append(f"  Uncached: {al['uncached_ms']:.3f}ms per lookup")
        report.append(f"  Cached:   {al['cached_ms']:.3f}ms per lookup")
        report.append(f"  Speedup:  {al['speedup']:.1f}x")
        report.append("  ✓ PASS" if al["speedup"] > 10 else "  ✗ FAIL (expected >10x)")

    # Policy projection
    if "policy_projection" in results:
        pp = results["policy_projection"]
        report.append("\n" + "-" * 70)
        report.append("2. POLICY PROJECTION")
        report.append("-" * 70)
        report.append(f"  Throughput: {pp['policies_per_sec']:.1f} policies/sec")
        report.append(f"  Time per policy: {pp['ms_per_policy']:.2f}ms")
        report.append(f"  Memory per policy: {pp['memory_per_policy_mb']:.4f} MB")
        report.append("  ✓ PASS" if pp["policies_per_sec"] > 50 else "  ✗ FAIL (expected >50/sec)")

    # Distributed processing
    if "distributed_processing" in results:
        dp = results["distributed_processing"]
        report.append("\n" + "-" * 70)
        report.append("3. DISTRIBUTED PROCESSING SCALABILITY")
        report.append("-" * 70)
        for r in dp:
            report.append(f"\n  Portfolio: {r['n_policies']:,} policies")
            report.append(f"    Duration: {r['duration_sec']:.2f}s")
            report.append(f"    Throughput: {r['throughput']:.1f} policies/sec")
            report.append(f"    Chunks: {r['chunks']}")
            report.append(f"    Peak memory: {r['peak_memory_mb']:.2f} MB")
            report.append(f"    Memory/policy: {r['memory_per_policy_mb']:.4f} MB")

    # Resource monitoring
    if "resource_monitoring" in results:
        rm = results["resource_monitoring"]
        report.append("\n" + "-" * 70)
        report.append("4. RESOURCE MONITORING OVERHEAD")
        report.append("-" * 70)
        report.append(f"  Snapshot time: {rm['snapshot_ms']:.3f}ms")
        report.append(f"  Chunk calc time: {rm['chunk_calc_ms']:.3f}ms")
        report.append(f"  Overhead: {rm['overhead_pct']:.3f}%")
        report.append("  ✓ PASS" if rm["overhead_pct"] < 1.0 else "  ✗ FAIL (expected <1%)")

    report.append("\n" + "=" * 70)
    report.append("END OF REPORT")
    report.append("=" * 70)

    report_text = "\n".join(report)
    print(report_text)

    if output_file:
        with open(output_file, "w") as f:
            f.write(report_text)
        print(f"\nReport saved to: {output_file}")

        # Also save JSON
        json_file = output_file.replace(".txt", ".json")
        with open(json_file, "w") as f:
            json.dump(results, f, indent=2)
        print(f"JSON data saved to: {json_file}")

    return report_text


def main():
    """Run all benchmarks."""
    parser = argparse.ArgumentParser(description="Run performance benchmarks")
    parser.add_argument(
        "--assumptions-dir", default="data/assumptions", help="Path to assumptions directory"
    )
    parser.add_argument("--output", default="benchmark_results.txt", help="Output file for report")
    parser.add_argument(
        "--quick", action="store_true", help="Run quick benchmarks (fewer iterations)"
    )

    args = parser.parse_args()

    # Adjust iterations for quick mode
    lookup_iterations = 100 if args.quick else 1000
    projection_policies = 50 if args.quick else 100
    portfolio_sizes = [1000, 5000] if args.quick else [1000, 5000, 10000]
    monitor_iterations = 100 if args.quick else 1000

    print("\n" + "=" * 70)
    print("STARTING PERFORMANCE BENCHMARKS")
    print("=" * 70)
    print(f"Mode: {'QUICK' if args.quick else 'FULL'}")
    print(f"Assumptions directory: {args.assumptions_dir}")

    results = {}

    try:
        # 1. Assumption lookups
        results["assumption_lookups"] = benchmark_assumption_lookups(
            args.assumptions_dir, n_iterations=lookup_iterations
        )

        # 2. Policy projection
        results["policy_projection"] = benchmark_policy_projection(
            args.assumptions_dir, n_policies=projection_policies
        )

        # 3. Distributed processing
        results["distributed_processing"] = benchmark_distributed_processing(
            portfolio_sizes=portfolio_sizes
        )

        # 4. Resource monitoring
        results["resource_monitoring"] = benchmark_resource_monitoring(
            n_iterations=monitor_iterations
        )

        # Generate report
        generate_benchmark_report(results, args.output)

        print("\n✓ All benchmarks completed successfully!")

    except Exception as e:
        print(f"\n✗ Benchmark failed: {e}")
        import traceback

        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
