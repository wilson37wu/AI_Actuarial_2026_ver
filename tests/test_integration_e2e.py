"""
End-to-end integration tests for complete ALM/TVOG workflow.

Tests the full pipeline:
1. Load assumptions via FlexibleAssumptionProvider
2. Generate ESG scenarios
3. Project policies via AssetShareEngine
4. Run distributed processing via DistributedExecutor
5. Validate results and reconciliation
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import tempfile
import time

import numpy as np
import pandas as pd
import pytest
from par_model_v2.assumptions.flexible_provider import FlexibleAssumptionProvider
from par_model_v2.utils.memory_profiler import MemoryTracker
from par_model_v2.utils.resource_monitor import ResourceMonitor
from par_model_v2.valuation.asset_share_engine import AssetShareConfig, AssetShareEngine
from par_model_v2.valuation.distributed_executor import DistributedConfig, DistributedExecutor


@pytest.fixture
def sample_assumptions_dir(tmp_path):
    """Create sample assumption files for testing."""
    assumptions_dir = tmp_path / "assumptions"
    assumptions_dir.mkdir()

    # Create metadata.json
    metadata = {
        "mortality_qx": {
            "file": "mortality_qx.csv",
            "dimensions": ["product", "gender", "age", "policy_year"],
            "value_column": "qx",
            "interpolation": "linear",
            "extrapolation": "constant",
        },
        "lapse": {
            "file": "lapse.csv",
            "dimensions": ["product", "policy_year"],
            "value_column": "lapse_rate",
            "interpolation": "step",
            "extrapolation": "constant",
        },
        "expense": {
            "file": "expense.csv",
            "dimensions": ["product", "policy_year"],
            "value_column": "expense_amount",
            "interpolation": "step",
            "extrapolation": "constant",
        },
    }

    import json

    with open(assumptions_dir / "metadata.json", "w") as f:
        json.dump(metadata, f)

    # Create mortality table
    mortality_data = []
    for product in ["WL", "Pension"]:
        for gender in ["M", "F"]:
            for age in [25, 30, 35, 40, 45, 50]:
                for py in [1, 2, 3, 5]:
                    qx = 0.0005 * (age / 25) * (1.5 if gender == "M" else 1.0)
                    mortality_data.append(
                        {
                            "product": product,
                            "gender": gender,
                            "age": age,
                            "policy_year": py,
                            "qx": qx,
                        }
                    )
    pd.DataFrame(mortality_data).to_csv(assumptions_dir / "mortality_qx.csv", index=False)

    # Create lapse table
    lapse_data = []
    for product in ["WL", "Pension"]:
        for py in [1, 2, 3, 5, 10]:
            lapse_rate = 0.15 / py * (0.8 if product == "Pension" else 1.0)
            lapse_data.append({"product": product, "policy_year": py, "lapse_rate": lapse_rate})
    pd.DataFrame(lapse_data).to_csv(assumptions_dir / "lapse.csv", index=False)

    # Create expense table
    expense_data = []
    for product in ["WL", "Pension"]:
        for py in [1, 2, 3, 5, 10]:
            expense = 500 if py == 1 else 50
            expense_data.append({"product": product, "policy_year": py, "expense_amount": expense})
    pd.DataFrame(expense_data).to_csv(assumptions_dir / "expense.csv", index=False)

    return assumptions_dir


@pytest.fixture
def sample_policies():
    """Create sample policy portfolio."""
    np.random.seed(42)

    n_policies = 1000

    policies = pd.DataFrame(
        {
            "policy_id": [f"POL{i:06d}" for i in range(n_policies)],
            "product": np.random.choice(["WL", "Pension"], n_policies),
            "gender": np.random.choice(["M", "F"], n_policies),
            "age": np.random.randint(25, 60, n_policies),
            "smoker_status": np.random.choice(["Y", "N"], n_policies, p=[0.2, 0.8]),
            "sum_assured": np.random.randint(100000, 1000000, n_policies),
            "annual_premium": np.random.randint(5000, 50000, n_policies),
            "premium_term": 20,
            "maturity_term": 360,
            "initial_asset_share": 0.0,
        }
    )

    return policies


@pytest.fixture
def sample_investment_returns():
    """Create sample investment return series."""
    np.random.seed(42)
    n_timesteps = 360

    # Monthly returns: 6% annual = 0.5% monthly with 2% volatility
    returns = pd.Series(np.random.normal(0.06 / 12, 0.02 / 12, n_timesteps))

    return returns


class TestEndToEndIntegration:
    """End-to-end integration test suite."""

    def test_complete_workflow_small_portfolio(
        self, sample_assumptions_dir, sample_policies, sample_investment_returns
    ):
        """
        Test complete workflow with small portfolio (1000 policies).

        This test validates:
        1. Assumption loading
        2. Asset share projection
        3. Result aggregation
        4. Reconciliation
        """
        with MemoryTracker("E2E Small Portfolio") as tracker:
            # Step 1: Load assumptions
            provider = FlexibleAssumptionProvider(str(sample_assumptions_dir))

            assert "mortality_qx" in provider.tables
            assert "lapse" in provider.tables
            assert "expense" in provider.tables

            # Step 2: Configure asset share engine
            config = AssetShareConfig(
                policyholder_share=0.70, shareholder_share=0.30, lifetime_shareholder_cap=0.15
            )
            engine = AssetShareEngine(config)

            # Step 3: Project single policy (validation)
            test_policy = sample_policies.iloc[0]

            # Get assumption series
            mortality_rates = pd.Series(
                [
                    provider.get_mortality(
                        test_policy["product"],
                        test_policy["gender"],
                        test_policy["age"] + t // 12,
                        test_policy["smoker_status"],
                        t // 12 + 1,
                    )
                    / 12
                    for t in range(360)
                ]
            )

            lapse_rates = pd.Series(
                [
                    provider.get_value(
                        "lapse", product=test_policy["product"], policy_year=t // 12 + 1
                    )
                    / 12
                    for t in range(360)
                ]
            )

            expenses = pd.Series(
                [
                    provider.get_value(
                        "expense", product=test_policy["product"], policy_year=t // 12 + 1
                    )
                    / 12
                    for t in range(360)
                ]
            )

            # Project single policy
            result = engine.project_policy(
                policy=test_policy,
                investment_returns=sample_investment_returns,
                mortality_rates=mortality_rates,
                lapse_rates=lapse_rates,
                expenses=expenses,
                n_timesteps=360,
            )

            # Validate results
            assert len(result.policy_states) > 0
            assert len(result.cashflows) > 0
            assert "total_premiums" in result.summary_metrics
            assert "total_shareholder_profit" in result.summary_metrics

            # Validate profit sharing cap
            total_premiums = result.summary_metrics["total_premiums"]
            total_sh_profit = result.summary_metrics["total_shareholder_profit"]

            if total_premiums > 0:
                sh_margin = total_sh_profit / total_premiums
                assert sh_margin <= config.lifetime_shareholder_cap + 0.01  # Allow small tolerance

        # Check memory usage
        assert tracker.peak_mb < 500  # Should use less than 500MB for 1 policy

    def test_distributed_processing_integration(self, sample_assumptions_dir, sample_policies):
        """
        Test distributed processing with checkpoint/resume.

        This test validates:
        1. Distributed executor integration
        2. Chunk processing
        3. Result aggregation
        4. Resource monitoring
        """
        with tempfile.TemporaryDirectory() as checkpoint_dir:
            # Configure distributed executor
            config = DistributedConfig(
                chunk_size_auto=False,
                chunk_size_manual=100,
                checkpoint_dir=checkpoint_dir,
                save_intermediate=True,
                max_workers=2,
                verbose=False,
            )
            executor = DistributedExecutor(config)

            # Define processing function
            def process_chunk(chunk_df):
                """Process a chunk of policies."""
                results = []
                for _, policy in chunk_df.iterrows():
                    # Simple calculation for testing
                    result = {
                        "policy_id": policy["policy_id"],
                        "premium": policy["annual_premium"],
                        "sum_assured": policy["sum_assured"],
                    }
                    results.append(result)
                return results

            # Execute distributed processing
            start_time = time.time()
            result = executor.execute(
                data=sample_policies,
                process_func=process_chunk,
                avg_item_memory_mb=1.0,
                resume=False,
            )
            duration = time.time() - start_time

            # Validate results
            assert result.chunks_completed > 0
            assert result.chunks_failed == 0
            assert result.total_items == len(sample_policies)
            assert len(result.results) == result.chunks_completed

            # Validate performance
            throughput = result.total_items / duration
            assert throughput > 100  # At least 100 policies/sec

            # Validate checkpoint exists
            checkpoint_path = Path(checkpoint_dir) / "checkpoint.json"
            assert checkpoint_path.exists()

            # Test resume functionality
            result2 = executor.execute(
                data=sample_policies,
                process_func=process_chunk,
                avg_item_memory_mb=1.0,
                resume=True,
            )

            # Should not process any chunks (all already completed)
            assert result2.chunks_completed == 0

    def test_resource_monitoring_integration(self):
        """
        Test resource monitoring during processing.

        This test validates:
        1. Resource snapshot collection
        2. Chunk size calculation
        3. Resource limit enforcement
        """
        monitor = ResourceMonitor(max_ram_pct=0.90, max_cpu_pct=0.90)

        # Get initial snapshot
        snapshot1 = monitor.get_snapshot()
        assert snapshot1.ram_total_gb > 0
        assert snapshot1.cpu_count > 0

        # Calculate optimal chunk size
        chunk_size = monitor.calculate_optimal_chunk_size(total_items=10000, avg_item_memory_mb=5.0)

        assert monitor.min_chunk_size <= chunk_size <= monitor.max_chunk_size

        # Check resource limits
        is_ok, message = monitor.is_within_limits()
        assert isinstance(is_ok, bool)
        assert isinstance(message, str)

        # Get resource summary
        summary = monitor.get_resource_summary()
        assert "ram_total_gb" in summary
        assert "cpu_percent" in summary

        # Get history summary
        for _ in range(10):
            monitor.get_snapshot()
            time.sleep(0.01)

        history = monitor.get_history_summary(last_n=10)
        assert "ram_mean" in history
        assert "cpu_mean" in history

    def test_full_pipeline_with_reconciliation(
        self, sample_assumptions_dir, sample_policies, sample_investment_returns
    ):
        """
        Test full pipeline with cashflow reconciliation.

        This test validates:
        1. Complete workflow
        2. Cashflow reconciliation
        3. Asset share consistency
        """
        # Load assumptions
        provider = FlexibleAssumptionProvider(str(sample_assumptions_dir))

        # Configure engines
        asset_config = AssetShareConfig()
        asset_engine = AssetShareEngine(asset_config)

        # Process small sample
        sample = sample_policies.head(10)

        all_results = []
        for _, policy in sample.iterrows():
            # Get assumptions
            mortality_rates = pd.Series(
                [
                    provider.get_mortality(
                        policy["product"],
                        policy["gender"],
                        policy["age"] + t // 12,
                        policy["smoker_status"],
                        t // 12 + 1,
                    )
                    / 12
                    for t in range(360)
                ]
            )

            lapse_rates = pd.Series(
                [
                    provider.get_value("lapse", product=policy["product"], policy_year=t // 12 + 1)
                    / 12
                    for t in range(360)
                ]
            )

            expenses = pd.Series(
                [
                    provider.get_value(
                        "expense", product=policy["product"], policy_year=t // 12 + 1
                    )
                    / 12
                    for t in range(360)
                ]
            )

            # Project policy
            result = asset_engine.project_policy(
                policy=policy,
                investment_returns=sample_investment_returns,
                mortality_rates=mortality_rates,
                lapse_rates=lapse_rates,
                expenses=expenses,
                n_timesteps=360,
            )

            all_results.append(result)

        # Aggregate results
        total_premiums = sum(r.summary_metrics["total_premiums"] for r in all_results)
        total_benefits = sum(r.summary_metrics["total_benefits"] for r in all_results)
        total_expenses = sum(r.summary_metrics["total_expenses"] for r in all_results)
        total_sh_profit = sum(r.summary_metrics["total_shareholder_profit"] for r in all_results)

        # Validate aggregation
        assert total_premiums > 0
        assert total_benefits >= 0
        assert total_expenses > 0
        assert total_sh_profit >= 0

        # Validate profit margin is reasonable
        if total_premiums > 0:
            profit_margin = total_sh_profit / total_premiums
            assert 0 <= profit_margin <= 0.20  # Should be between 0% and 20%


class TestPerformanceBaseline:
    """Performance baseline tests for benchmarking."""

    def test_assumption_lookup_performance(self, sample_assumptions_dir):
        """Benchmark assumption lookup performance."""
        provider = FlexibleAssumptionProvider(str(sample_assumptions_dir))

        # Warm up cache
        for _ in range(10):
            provider.get_mortality("WL", "M", 35, "N", 1)

        # Benchmark uncached lookup
        start = time.time()
        for _ in range(1000):
            provider.clear_cache()
            provider.get_mortality("WL", "M", 35, "N", 1)
        uncached_time = time.time() - start

        # Benchmark cached lookup
        start = time.time()
        for _ in range(1000):
            provider.get_mortality("WL", "M", 35, "N", 1)
        cached_time = time.time() - start

        # Cached should be much faster
        speedup = uncached_time / cached_time
        assert speedup > 10  # At least 10x faster

        print("\nAssumption lookup performance:")
        print(f"  Uncached: {uncached_time * 1000:.2f}ms for 1000 lookups")
        print(f"  Cached: {cached_time * 1000:.2f}ms for 1000 lookups")
        print(f"  Speedup: {speedup:.1f}x")

    def test_policy_projection_performance(
        self, sample_assumptions_dir, sample_policies, sample_investment_returns
    ):
        """Benchmark single policy projection performance."""
        provider = FlexibleAssumptionProvider(str(sample_assumptions_dir))
        engine = AssetShareEngine(AssetShareConfig())

        policy = sample_policies.iloc[0]

        # Get assumptions
        mortality_rates = pd.Series(
            [
                provider.get_mortality(
                    policy["product"],
                    policy["gender"],
                    policy["age"] + t // 12,
                    policy["smoker_status"],
                    t // 12 + 1,
                )
                / 12
                for t in range(360)
            ]
        )
        lapse_rates = pd.Series(
            [
                provider.get_value("lapse", product=policy["product"], policy_year=t // 12 + 1) / 12
                for t in range(360)
            ]
        )
        expenses = pd.Series(
            [
                provider.get_value("expense", product=policy["product"], policy_year=t // 12 + 1)
                / 12
                for t in range(360)
            ]
        )

        # Benchmark projection
        start = time.time()
        n_runs = 100
        for _ in range(n_runs):
            engine.project_policy(
                policy=policy,
                investment_returns=sample_investment_returns,
                mortality_rates=mortality_rates,
                lapse_rates=lapse_rates,
                expenses=expenses,
                n_timesteps=360,
            )
        duration = time.time() - start

        policies_per_sec = n_runs / duration

        print("\nPolicy projection performance:")
        print(f"  {policies_per_sec:.1f} policies/sec (single core)")
        print(f"  {duration / n_runs * 1000:.2f}ms per policy")

        # Should project at least 50 policies/sec
        assert policies_per_sec > 50


class TestScalability:
    """Scalability tests with varying portfolio sizes."""

    @pytest.mark.parametrize("n_policies", [100, 1000, 5000])
    def test_scalability_by_portfolio_size(self, n_policies, sample_assumptions_dir):
        """Test scalability with different portfolio sizes."""
        # Generate policies
        np.random.seed(42)
        policies = pd.DataFrame(
            {
                "policy_id": [f"POL{i:06d}" for i in range(n_policies)],
                "product": np.random.choice(["WL", "Pension"], n_policies),
                "value": np.random.randn(n_policies),
            }
        )

        # Configure executor
        config = DistributedConfig(chunk_size_auto=True, max_workers=2, verbose=False)
        executor = DistributedExecutor(config)

        # Simple processing function
        def process_chunk(chunk_df):
            return [
                {"policy_id": row["policy_id"], "result": row["value"] * 2}
                for _, row in chunk_df.iterrows()
            ]

        # Execute and measure
        with MemoryTracker(f"Scalability {n_policies} policies", log_results=False) as tracker:
            start = time.time()
            result = executor.execute(
                data=policies, process_func=process_chunk, avg_item_memory_mb=1.0, resume=False
            )
            duration = time.time() - start

        throughput = n_policies / duration
        memory_per_policy = tracker.peak_mb / n_policies

        print(f"\nScalability test ({n_policies} policies):")
        print(f"  Duration: {duration:.2f}s")
        print(f"  Throughput: {throughput:.1f} policies/sec")
        print(f"  Peak memory: {tracker.peak_mb:.2f} MB")
        print(f"  Memory per policy: {memory_per_policy:.4f} MB")

        # Validate results
        assert result.chunks_completed > 0
        assert result.chunks_failed == 0
        assert throughput > 100  # At least 100 policies/sec


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
