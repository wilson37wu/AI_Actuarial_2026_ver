"""Distributed deterministic liability valuation for large portfolios.

This script processes very large synthetic portfolios (e.g., 1M+ policies) using
multi-processing to parallelize GPV calculations and cash-flow generation across
multiple CPU cores.

Usage
-----
Basic usage with defaults (no arguments required):

    python scripts/run_liability_distributed.py

This will automatically:
- Load portfolio from: data/inforce/synthetic_portfolio.parquet
- Save results to: data/liability_results
- Use all available CPU cores
- Apply default valuation assumptions

Custom portfolio and output paths:

    python scripts/run_liability_distributed.py \\
        --portfolio data/inforce/synthetic_portfolio.parquet \\
        --output_dir data/liability_results

Custom chunking and process count:

    python scripts/run_liability_distributed.py \\
        --chunk_size 100000 \\
        --n_processes 8 \\
        --discount_rate 0.03 \\
        --expense_loading 0.05 \\
        --rb_growth_rate 0.02 \\
        --surrender_rate 0.01 \\
        --save_policy_cashflows

Performance Notes
-----------------
- Chunk size: Balance between overhead and memory usage (default: 50,000)
- Process count: Typically set to number of CPU cores (default: auto-detect)
- Memory: Each worker holds one chunk in memory; monitor total RAM usage
- I/O: Writing policy cash flows for 1M+ policies can be slow; use SSD storage

Outputs
-------
- portfolio_with_gpv.parquet: Enriched portfolio with per-policy GPV columns
- aggregate_cashflows.csv: Monthly aggregate cash flows by category
- gpv_summary.json: Summary statistics and run metadata
- policy_cashflows.parquet: Per-policy schedules (if --save_policy_cashflows)

Requirements
------------
- pandas, numpy, pyarrow
- Multi-core CPU for parallel processing
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Try to import psutil for RAM detection
try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from par_model_v2.assumptions import AssumptionProvider
from par_model_v2.liabilities.deterministic_liability import value_portfolio

# Constants for resource management
ESTIMATED_BYTES_PER_POLICY = 2000  # ~2 KB per policy (conservative estimate)
CPU_UTILISATION_RATIO = 0.9  # Use 90% of available cores
MIN_CHUNK_SIZE = 10_000
MAX_CHUNK_SIZE = 200_000
RAM_SAFETY_FACTOR = 0.5  # Use 50% of available RAM for chunk sizing


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    # Set default paths relative to project root
    default_portfolio = PROJECT_ROOT / "data" / "inforce" / "synthetic_portfolio.parquet"
    default_output_dir = PROJECT_ROOT / "data" / "liability_results"
    default_assumption_dir = PROJECT_ROOT / "data" / "assumptions"

    parser = argparse.ArgumentParser(
        description="Distributed deterministic liability valuation for large portfolios",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--portfolio",
        type=str,
        default=str(default_portfolio),
        help="Path to portfolio Parquet file",
    )

    parser.add_argument(
        "--output_dir",
        type=str,
        default=str(default_output_dir),
        help="Output directory for valuation results",
    )

    parser.add_argument(
        "--assumption_dir",
        type=str,
        default=str(default_assumption_dir),
        help="Directory containing assumption CSV tables",
    )

    parser.add_argument(
        "--chunk_size",
        type=int,
        default=None,
        help="Number of policies per chunk (default: auto-detect based on RAM)",
    )

    parser.add_argument(
        "--n_processes",
        type=int,
        default=None,
        help="Number of worker processes (default: CPU count)",
    )

    parser.add_argument(
        "--discount_rate",
        type=float,
        default=0.03,
        help="Annual discount rate",
    )

    parser.add_argument(
        "--expense_loading",
        type=float,
        default=0.05,
        help="Expense loading as fraction of premiums",
    )

    parser.add_argument(
        "--rb_growth_rate",
        type=float,
        default=0.02,
        help="Annual reversionary bonus growth rate",
    )

    parser.add_argument(
        "--surrender_rate",
        type=float,
        default=0.01,
        help="Annual surrender/lapse rate",
    )

    parser.add_argument(
        "--valuation_year",
        type=int,
        default=2025,
        help="Valuation year",
    )

    parser.add_argument(
        "--max_projection_years",
        type=int,
        default=100,
        help="Maximum projection horizon in years",
    )

    parser.add_argument(
        "--save_policy_cashflows",
        action="store_true",
        help="Save per-policy cash-flow schedules to Parquet",
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from previous run using checkpoints",
    )

    return parser.parse_args()


def process_chunk(
    chunk_data: Tuple[int, pd.DataFrame],
    params: Dict[str, Any],
    checkpoint_dir: str,
    assumption_dir: Optional[str] = None,
) -> Tuple[int, pd.DataFrame, pd.DataFrame, Dict[str, float], bool]:
    """Process a single chunk of policies with checkpoint support.

    Parameters
    ----------
    chunk_data:
        Tuple of (chunk_id, chunk_df)
    params:
        Valuation parameters
    checkpoint_dir:
        Checkpoint directory (as string for pickle safety)
    assumption_dir:
        Assumption directory (as string, optional)

    Returns
    -------
    tuple
        (chunk_id, df_result, aggregate_cf, summary, success)
    """
    chunk_id, chunk_df = chunk_data

    # Convert string paths to Path objects inside worker (Path already imported at module level)
    checkpoint_path = Path(checkpoint_dir)
    assumption_path = Path(assumption_dir) if assumption_dir else None

    try:
        # Load provider if assumption_dir is provided
        provider = None
        if assumption_path and assumption_path.exists():
            from par_model_v2.assumptions import AssumptionProvider

            provider = AssumptionProvider(assumption_path)

        # Call value_portfolio on this chunk
        # Note: output_dir=None to avoid writing intermediate files
        df_result, aggregate_cf, summary = value_portfolio(
            chunk_df,
            discount_rate=params["discount_rate"],
            expense_loading=params["expense_loading"],
            rb_growth_rate=params["rb_growth_rate"],
            surrender_rate=params["surrender_rate"],
            valuation_year=params["valuation_year"],
            max_projection_years=params["max_projection_years"],
            output_dir=None,  # Don't write intermediate files
            save_cashflows=False,  # We'll aggregate first
            provider=provider,
        )

        # Write checkpoint on success
        checkpoint_file = checkpoint_path / f"chunk_{chunk_id:05d}.done"
        checkpoint_file.write_text(
            json.dumps(
                {
                    "chunk_id": chunk_id,
                    "n_policies": len(chunk_df),
                    "completed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        )

        return (chunk_id, df_result, aggregate_cf, summary, True)

    except Exception as e:
        # Write failure checkpoint
        checkpoint_file = checkpoint_path / f"chunk_{chunk_id:05d}.failed"
        checkpoint_file.write_text(
            json.dumps(
                {
                    "chunk_id": chunk_id,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                    "failed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        )
        print(f"\nError processing chunk {chunk_id}: {e}")
        return (chunk_id, None, None, None, False)


def aggregate_results(
    chunk_results: list,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, float]]:
    """Aggregate results from all chunks.

    Parameters
    ----------
    chunk_results:
        List of (chunk_id, df_result, aggregate_cf, summary) tuples

    Returns
    -------
    tuple
        (final_df_result, final_aggregate_cf, final_summary)
    """

    # Sort by chunk_id to maintain order
    chunk_results.sort(key=lambda x: x[0])

    # Concatenate all df_result DataFrames
    all_df_results = [result[1] for result in chunk_results]
    final_df_result = pd.concat(all_df_results, axis=0, ignore_index=True)

    # Aggregate cash flows by month
    all_aggregate_cfs = [result[2] for result in chunk_results]
    combined_cf = pd.concat(all_aggregate_cfs, axis=0, ignore_index=True)

    final_aggregate_cf = (
        combined_cf.groupby(["month_index", "date"])
        .agg(
            {
                "total_premium": "sum",
                "total_expense": "sum",
                "total_surrender": "sum",
                "total_death": "sum",
                "total_guaranteed": "sum",
                "total_non_guaranteed": "sum",
            }
        )
        .reset_index()
        .sort_values("month_index")
        .reset_index(drop=True)
    )

    # Sum summary statistics
    final_summary = {
        "total_pv_premiums": sum(result[3]["total_pv_premiums"] for result in chunk_results),
        "total_pv_benefits": sum(result[3]["total_pv_benefits"] for result in chunk_results),
        "total_gpv": sum(result[3]["total_gpv"] for result in chunk_results),
        "n_policies": sum(result[3]["n_policies"] for result in chunk_results),
    }

    return final_df_result, final_aggregate_cf, final_summary


def get_available_ram_gb() -> float:
    """Get available system RAM in GB."""
    if PSUTIL_AVAILABLE:
        return psutil.virtual_memory().available / (1024**3)
    return None


def compute_chunk_size(
    user_chunk_size: int,
    available_ram_gb: float,
    n_policies: int,
) -> int:
    """Compute safe chunk size based on available RAM.

    Parameters
    ----------
    user_chunk_size:
        User-provided chunk size (or None)
    available_ram_gb:
        Available RAM in GB
    n_policies:
        Total number of policies

    Returns
    -------
    int
        Safe chunk size
    """

    if user_chunk_size is not None:
        # User override - respect it but apply bounds
        return max(MIN_CHUNK_SIZE, min(user_chunk_size, MAX_CHUNK_SIZE))

    if available_ram_gb is None:
        # No RAM detection - use conservative default
        return 50_000

    # Compute based on available RAM
    available_bytes = available_ram_gb * (1024**3)
    max_policies_by_ram = int(available_bytes * RAM_SAFETY_FACTOR / ESTIMATED_BYTES_PER_POLICY)

    # Apply bounds
    chunk_size = max(MIN_CHUNK_SIZE, min(max_policies_by_ram, MAX_CHUNK_SIZE))

    # Don't exceed total policies
    chunk_size = min(chunk_size, n_policies)

    return chunk_size


def get_checkpoint_status(checkpoint_dir: Path, n_chunks: int) -> Dict[str, Any]:
    """Scan checkpoint directory and return status.

    Parameters
    ----------
    checkpoint_dir:
        Checkpoint directory
    n_chunks:
        Total number of chunks

    Returns
    -------
    dict
        Status with completed, failed, and pending chunk IDs
    """

    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    completed = set()
    failed = set()

    for checkpoint_file in checkpoint_dir.glob("chunk_*.done"):
        chunk_id = int(checkpoint_file.stem.split("_")[1])
        completed.add(chunk_id)

    for checkpoint_file in checkpoint_dir.glob("chunk_*.failed"):
        chunk_id = int(checkpoint_file.stem.split("_")[1])
        failed.add(chunk_id)

    pending = set(range(n_chunks)) - completed - failed

    return {
        "completed": sorted(completed),
        "failed": sorted(failed),
        "pending": sorted(pending),
        "n_completed": len(completed),
        "n_failed": len(failed),
        "n_pending": len(pending),
    }


def save_outputs(
    df_result: pd.DataFrame,
    aggregate_cf: pd.DataFrame,
    summary: Dict[str, float],
    output_dir: Path,
    run_metadata: Dict[str, Any],
) -> None:
    """Save all output files.

    Parameters
    ----------
    df_result:
        Enriched portfolio with GPV columns
    aggregate_cf:
        Aggregate monthly cash flows
    summary:
        Summary statistics
    output_dir:
        Output directory
    run_metadata:
        Run configuration metadata
    """

    output_dir.mkdir(parents=True, exist_ok=True)

    # Save enriched portfolio
    portfolio_path = output_dir / "portfolio_with_gpv.parquet"
    df_result.to_parquet(portfolio_path, index=False)
    print(f"  Saved portfolio: {portfolio_path}")

    # Save aggregate cash flows
    cf_path = output_dir / "aggregate_cashflows.csv"
    aggregate_cf.to_csv(cf_path, index=False)
    print(f"  Saved cash flows: {cf_path}")

    # Save summary with metadata
    summary_with_meta = {**summary, "run_metadata": run_metadata}
    summary_path = output_dir / "gpv_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary_with_meta, f, indent=2)
    print(f"  Saved summary: {summary_path}")


def process_portfolio_with_cashflows(
    df_portfolio: pd.DataFrame,
    params: Dict[str, Any],
    chunk_size: int,
    n_processes: int,
    output_dir: Path,
    resume: bool = False,
    assumption_dir: Optional[Path] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, float]]:
    """Process portfolio and save per-policy cash flows incrementally.

    This function processes chunks sequentially to avoid memory issues
    when saving per-policy cash flows for very large portfolios.

    Parameters
    ----------
    df_portfolio:
        Full portfolio DataFrame
    params:
        Valuation parameters
    chunk_size:
        Chunk size
    n_processes:
        Not used in this mode (sequential processing)
    output_dir:
        Output directory
    resume:
        Whether to resume from checkpoints
    assumption_dir:
        Assumption directory (optional)

    Returns
    -------
    tuple
        (final_df_result, final_aggregate_cf, final_summary)
    """

    print("\nProcessing with per-policy cash flows (sequential mode)...")

    n_policies = len(df_portfolio)
    n_chunks = (n_policies + chunk_size - 1) // chunk_size

    all_df_results = []
    all_aggregate_cfs = []
    all_summaries = []
    policy_cf_path = output_dir / "policy_cashflows.parquet"

    for i in range(n_chunks):
        start_idx = i * chunk_size
        end_idx = min((i + 1) * chunk_size, n_policies)
        chunk_df = df_portfolio.iloc[start_idx:end_idx].copy()

        # Skip if already completed
        checkpoint_file = output_dir / "checkpoints" / f"chunk_{i:05d}.done"
        if checkpoint_file.exists() and resume:
            print(f"  Skipping chunk {i + 1}/{n_chunks} (already completed)")
            # Load existing results for aggregation
            # Note: In sequential mode, we need to regenerate for aggregation
            # This is a limitation - consider storing intermediate results
            continue

        print(f"  Processing chunk {i + 1}/{n_chunks} ({len(chunk_df):,} policies)...")

        # Process chunk with cash flows
        df_result, aggregate_cf, summary = value_portfolio(
            chunk_df,
            discount_rate=params["discount_rate"],
            expense_loading=params["expense_loading"],
            rb_growth_rate=params["rb_growth_rate"],
            surrender_rate=params["surrender_rate"],
            valuation_year=params["valuation_year"],
            max_projection_years=params["max_projection_years"],
            output_dir=None,
            save_cashflows=False,  # We'll handle this manually
            provider=AssumptionProvider(Path(assumption_dir)) if assumption_dir else None,
        )

        all_df_results.append(df_result)
        all_aggregate_cfs.append(aggregate_cf)
        all_summaries.append(summary)

        # Write checkpoint
        checkpoint_file.write_text(
            json.dumps(
                {
                    "chunk_id": i,
                    "n_policies": len(chunk_df),
                    "completed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        )

        # Generate and append policy cash flows
        from par_model_v2.liabilities.deterministic_liability import (
            generate_monthly_cashflows,
        )

        chunk_cashflows = []
        for _, row in chunk_df.iterrows():
            policy_dict = row.to_dict()
            cf = generate_monthly_cashflows(
                policy_dict,
                discount_rate=params["discount_rate"],
                expense_loading=params["expense_loading"],
                rb_growth_rate=params["rb_growth_rate"],
                surrender_rate=params["surrender_rate"],
                valuation_year=params["valuation_year"],
                max_projection_years=params["max_projection_years"],
            )
            chunk_cashflows.append(cf)

        # Append to Parquet file
        chunk_cf_df = pd.concat(chunk_cashflows, axis=0, ignore_index=True)
        if i == 0:
            chunk_cf_df.to_parquet(policy_cf_path, index=False)
        else:
            # Append mode
            existing = pd.read_parquet(policy_cf_path)
            combined = pd.concat([existing, chunk_cf_df], axis=0, ignore_index=True)
            combined.to_parquet(policy_cf_path, index=False)

    print(f"  Saved policy cash flows: {policy_cf_path}")

    # Aggregate results
    final_df_result = pd.concat(all_df_results, axis=0, ignore_index=True)

    combined_cf = pd.concat(all_aggregate_cfs, axis=0, ignore_index=True)
    final_aggregate_cf = (
        combined_cf.groupby(["month_index", "date"])
        .agg(
            {
                "total_premium": "sum",
                "total_expense": "sum",
                "total_surrender": "sum",
                "total_death": "sum",
                "total_guaranteed": "sum",
                "total_non_guaranteed": "sum",
            }
        )
        .reset_index()
        .sort_values("month_index")
        .reset_index(drop=True)
    )

    final_summary = {
        "total_pv_premiums": sum(s["total_pv_premiums"] for s in all_summaries),
        "total_pv_benefits": sum(s["total_pv_benefits"] for s in all_summaries),
        "total_gpv": sum(s["total_gpv"] for s in all_summaries),
        "n_policies": sum(s["n_policies"] for s in all_summaries),
    }

    return final_df_result, final_aggregate_cf, final_summary


def main() -> None:
    """Main entry point for distributed liability valuation."""

    args = parse_args()

    # Validate inputs
    portfolio_path = Path(args.portfolio)
    if not portfolio_path.exists():
        raise FileNotFoundError(f"Portfolio file not found: {portfolio_path}")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    assumption_dir = Path(args.assumption_dir)
    if not assumption_dir.exists():
        print(f"\n⚠ Warning: Assumption directory not found: {assumption_dir}")
        print("  Using hardcoded assumptions. Run: python scripts/generate_sample_assumptions.py")
        assumption_dir = None
    else:
        print(f"\n✓ Loading assumptions from: {assumption_dir}")

    # Determine number of processes with 90% CPU cap
    detected_cores = mp.cpu_count()
    if args.n_processes is None:
        # Apply 90% cap
        n_processes = max(1, int(CPU_UTILISATION_RATIO * detected_cores))
        cpu_capped = True
    else:
        # User override
        n_processes = args.n_processes
        cpu_capped = False

    print("=" * 70)
    print("Distributed Deterministic Liability Valuation")
    print("=" * 70)

    # Inform user of defaults if arguments were not provided
    default_portfolio = PROJECT_ROOT / "data" / "inforce" / "synthetic_portfolio.parquet"
    default_output_dir = PROJECT_ROOT / "data" / "liability_results"

    if args.portfolio == str(default_portfolio):
        print(f"\nUsing default portfolio: {portfolio_path}")
    if args.output_dir == str(default_output_dir):
        print(f"Using default output directory: {output_dir}")

    # Load portfolio
    print(f"\nLoading portfolio from: {portfolio_path}")
    start_load = time.time()
    df_portfolio = pd.read_parquet(portfolio_path)
    load_time = time.time() - start_load

    n_policies = len(df_portfolio)
    print(f"  Loaded {n_policies:,} policies in {load_time:.2f}s")

    # Display portfolio summary
    print("\nPortfolio Summary:")
    product_counts = df_portfolio["product_code"].value_counts()
    for product, count in product_counts.items():
        pct = 100 * count / n_policies
        print(f"  {product}: {count:,} ({pct:.1f}%)")

    # CPU configuration
    print("\n" + "-" * 70)
    print("CPU Configuration:")
    print("-" * 70)
    print(f"  CPU cores detected: {detected_cores}")
    if cpu_capped:
        print(f"  Worker processes (90% cap): {n_processes}")
    else:
        print(f"  Worker processes (user override): {n_processes}")

    # RAM-based chunk sizing
    available_ram_gb = get_available_ram_gb()
    if available_ram_gb is not None:
        print("\n" + "-" * 70)
        print("RAM Configuration:")
        print("-" * 70)
        print(f"  Available RAM: {available_ram_gb:.2f} GB")
        print(f"  Estimated RAM per policy: {ESTIMATED_BYTES_PER_POLICY / 1024:.2f} KB")

    # Compute chunk size
    chunk_size = compute_chunk_size(args.chunk_size, available_ram_gb, n_policies)
    if args.chunk_size is None:
        print(f"  Auto-selected chunk size: {chunk_size:,}")
    else:
        print(f"  User-specified chunk size: {chunk_size:,}")

    # Checkpoint setup
    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    # Valuation configuration
    print("\n" + "-" * 70)
    print("Valuation Configuration:")
    print("-" * 70)
    print(f"  Discount rate:      {args.discount_rate:.2%}")
    print(f"  Expense loading:    {args.expense_loading:.2%}")
    print(f"  RB growth rate:     {args.rb_growth_rate:.2%}")
    print(f"  Surrender rate:     {args.surrender_rate:.2%}")
    print(f"  Valuation year:     {args.valuation_year}")
    print(f"  Save policy CFs:    {args.save_policy_cashflows}")

    # Prepare parameters
    params = {
        "discount_rate": args.discount_rate,
        "expense_loading": args.expense_loading,
        "rb_growth_rate": args.rb_growth_rate,
        "surrender_rate": args.surrender_rate,
        "valuation_year": args.valuation_year,
        "max_projection_years": args.max_projection_years,
    }

    # Split into chunks
    n_chunks = (n_policies + chunk_size - 1) // chunk_size

    # Check for existing checkpoints
    checkpoint_status = get_checkpoint_status(checkpoint_dir, n_chunks)

    print("\n" + "-" * 70)
    print("Checkpoint Status:")
    print("-" * 70)
    print(f"  Total chunks:       {n_chunks}")
    print(f"  Completed chunks:   {checkpoint_status['n_completed']}")
    print(f"  Failed chunks:      {checkpoint_status['n_failed']}")
    print(f"  Pending chunks:     {checkpoint_status['n_pending']}")

    if checkpoint_status["n_completed"] > 0 and args.resume:
        print("\n  Resuming from previous run...")
        print(f"  Skipping {checkpoint_status['n_completed']} completed chunks")
    elif checkpoint_status["n_completed"] > 0 and not args.resume:
        print(f"\n  Warning: Found {checkpoint_status['n_completed']} completed chunks")
        print("  Use --resume to skip them, or delete checkpoints/ to start fresh")
        print("\n  Clearing checkpoints and starting fresh...")
        # Clear checkpoint directory
        import shutil

        shutil.rmtree(checkpoint_dir)
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        # Re-scan after clearing
        checkpoint_status = get_checkpoint_status(checkpoint_dir, n_chunks)
        print(f"  Checkpoints cleared. Ready to process {n_chunks} chunks.")

    # Process based on whether we need policy cash flows
    if args.save_policy_cashflows:
        # Sequential processing to avoid memory issues
        print("\nNote: Per-policy cash flows requested - using sequential processing")
        start_process = time.time()

        final_df_result, final_aggregate_cf, final_summary = process_portfolio_with_cashflows(
            df_portfolio,
            params,
            chunk_size,
            n_processes,
            output_dir,
            args.resume,
            Path(args.assumption_dir),
        )

        process_time = time.time() - start_process

    else:
        # Parallel processing
        print("\n" + "-" * 70)
        print("Processing chunks in parallel...")
        print("-" * 70)

        start_process = time.time()

        # Create chunk data (skip completed if resuming)
        chunks = []
        chunks_to_process = checkpoint_status["pending"] if args.resume else range(n_chunks)

        for i in chunks_to_process:
            start_idx = i * chunk_size
            end_idx = min((i + 1) * chunk_size, n_policies)
            chunk_df = df_portfolio.iloc[start_idx:end_idx].copy()
            chunks.append((i, chunk_df))

        if not chunks:
            print("\nAll chunks already completed!")
            print("Use output files or delete checkpoints/ to reprocess.")
            return

        # Process chunks in parallel
        chunk_results = []
        failed_chunks = []

        with ProcessPoolExecutor(max_workers=n_processes) as executor:
            # Pass strings to workers for pickle safety
            futures = {
                executor.submit(
                    process_chunk,
                    chunk,
                    params,
                    str(checkpoint_dir),
                    str(args.assumption_dir) if args.assumption_dir else None,
                ): chunk[0]
                for chunk in chunks
            }

            for future in as_completed(futures):
                chunk_id = futures[future]
                try:
                    result = future.result()
                    if result[4]:  # success flag
                        chunk_results.append(result[:4])  # exclude success flag
                        print(f"  Completed chunk {result[0] + 1}/{n_chunks}")
                    else:
                        failed_chunks.append(chunk_id)
                        print(f"  Failed chunk {chunk_id + 1}/{n_chunks} (continuing...)")
                except Exception as e:
                    failed_chunks.append(chunk_id)
                    print(f"  Error in chunk {chunk_id + 1}/{n_chunks}: {e}")
                    # Continue processing other chunks

        process_time = time.time() - start_process

        # Check if we have any successful results
        if not chunk_results:
            print("\nError: No chunks completed successfully!")
            print(f"Failed chunks: {len(failed_chunks)}")
            sys.exit(1)

        # Aggregate results
        print("\nAggregating results...")
        final_df_result, final_aggregate_cf, final_summary = aggregate_results(chunk_results)

        # Update checkpoint status after processing
        final_checkpoint_status = get_checkpoint_status(checkpoint_dir, n_chunks)

        # Report on failed chunks if any
        if failed_chunks:
            print(f"\n⚠ Warning: {len(failed_chunks)} chunk(s) failed")
            print(f"  Failed chunk IDs: {sorted(failed_chunks)}")
            print("\n  To retry failed chunks, run:")
            print("    python scripts/run_liability_distributed.py --resume")

    # Display results
    print("\n" + "=" * 70)
    print("Valuation Results")
    print("=" * 70)

    print(f"\nProcessing time: {process_time:.2f}s ({n_policies / process_time:,.0f} policies/sec)")

    # Chunk summary
    if not args.save_policy_cashflows:
        print("\nChunk Summary:")
        print(f"  Total chunks:       {n_chunks}")
        print(f"  Completed:          {final_checkpoint_status['n_completed']}")
        print(f"  Failed:             {final_checkpoint_status['n_failed']}")
        if args.resume and checkpoint_status["n_completed"] > 0:
            print(f"  Skipped (previous): {checkpoint_status['n_completed']}")

    print("\nAggregate Present Values:")
    print(f"  Total PV Premiums:  ${final_summary['total_pv_premiums']:>15,.2f}")
    print(f"  Total PV Benefits:  ${final_summary['total_pv_benefits']:>15,.2f}")
    print(f"  Total GPV:          ${final_summary['total_gpv']:>15,.2f}")
    print(f"  Number of Policies: {final_summary['n_policies']:>15,}")

    # Per-policy statistics
    print("\nPer-Policy GPV Statistics:")
    print(f"  Mean GPV:           ${final_df_result['gpv_policy'].mean():>15,.2f}")
    print(f"  Median GPV:         ${final_df_result['gpv_policy'].median():>15,.2f}")
    print(f"  Min GPV:            ${final_df_result['gpv_policy'].min():>15,.2f}")
    print(f"  Max GPV:            ${final_df_result['gpv_policy'].max():>15,.2f}")

    # Save outputs
    print("\n" + "-" * 70)
    print("Saving outputs...")
    print("-" * 70)

    run_metadata = {
        "chunk_size": chunk_size,
        "n_processes": n_processes,
        "n_chunks": n_chunks,
        "processing_time_seconds": process_time,
        "policies_per_second": n_policies / process_time,
        "discount_rate": args.discount_rate,
        "expense_loading": args.expense_loading,
        "rb_growth_rate": args.rb_growth_rate,
        "surrender_rate": args.surrender_rate,
        "valuation_year": args.valuation_year,
        "cpu_cores_detected": detected_cores,
        "cpu_utilisation_ratio": CPU_UTILISATION_RATIO if cpu_capped else None,
        "worker_processes_used": n_processes,
        "available_ram_gb": available_ram_gb,
        "estimated_bytes_per_policy": ESTIMATED_BYTES_PER_POLICY,
        "chunk_size_selected": chunk_size,
        "run_status": {
            "total_chunks": n_chunks,
            "completed_chunks": final_checkpoint_status["n_completed"]
            if not args.save_policy_cashflows
            else n_chunks,
            "failed_chunks": final_checkpoint_status["n_failed"]
            if not args.save_policy_cashflows
            else 0,
            "skipped_chunks": checkpoint_status["n_completed"] if args.resume else 0,
        },
    }

    save_outputs(final_df_result, final_aggregate_cf, final_summary, output_dir, run_metadata)

    print("\n" + "=" * 70)
    print("Valuation Complete!")
    print("=" * 70)
    print(f"\nResults saved to: {output_dir.resolve()}/")


if __name__ == "__main__":
    main()
