"""
Unit tests for distributed processing components.

Tests cover:
- ResourceMonitor functionality
- Memory profiling utilities
- DistributedExecutor with fault tolerance
- Checkpoint/resume functionality
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
from par_model_v2.utils.memory_profiler import (
    MemoryTracker,
    estimate_dataframe_memory,
    get_memory_usage,
    profile_memory,
)
from par_model_v2.utils.resource_monitor import ResourceMonitor, ResourceSnapshot
from par_model_v2.valuation.distributed_executor import (
    ChunkStatus,
    DistributedConfig,
    DistributedExecutor,
    ExecutionResult,
)


class TestResourceMonitor:
    """Test suite for ResourceMonitor."""

    def test_initialization(self):
        """Test monitor initialization."""
        monitor = ResourceMonitor(max_ram_pct=0.90, max_cpu_pct=0.90)

        assert monitor.max_ram_pct == 0.90
        assert monitor.max_cpu_pct == 0.90
        assert monitor.min_chunk_size > 0
        assert monitor.max_chunk_size > monitor.min_chunk_size

    def test_get_snapshot(self):
        """Test getting resource snapshot."""
        monitor = ResourceMonitor()
        snapshot = monitor.get_snapshot()

        assert isinstance(snapshot, ResourceSnapshot)
        assert snapshot.ram_total_gb > 0
        assert snapshot.ram_available_gb > 0
        assert 0 <= snapshot.ram_percent <= 100
        assert 0 <= snapshot.cpu_percent <= 100
        assert snapshot.cpu_count > 0

    def test_get_available_ram(self):
        """Test RAM availability check."""
        monitor = ResourceMonitor()
        available = monitor.get_available_ram_gb()

        assert available > 0
        assert isinstance(available, float)

    def test_get_ram_usage_pct(self):
        """Test RAM usage percentage."""
        monitor = ResourceMonitor()
        usage = monitor.get_ram_usage_pct()

        assert 0 <= usage <= 100

    def test_get_cpu_usage_pct(self):
        """Test CPU usage percentage."""
        monitor = ResourceMonitor()
        usage = monitor.get_cpu_usage_pct()

        assert 0 <= usage <= 100

    def test_is_ram_available(self):
        """Test RAM availability check."""
        monitor = ResourceMonitor()

        # Should have at least 0.1 GB available
        assert monitor.is_ram_available(0.1)

        # Should not have 1 TB available
        assert not monitor.is_ram_available(1000)

    def test_is_within_limits(self):
        """Test resource limit checking."""
        monitor = ResourceMonitor(max_ram_pct=1.0, max_cpu_pct=1.0)
        is_ok, message = monitor.is_within_limits()

        assert isinstance(is_ok, bool)
        assert isinstance(message, str)

    def test_calculate_optimal_chunk_size(self):
        """Test optimal chunk size calculation."""
        monitor = ResourceMonitor()

        chunk_size = monitor.calculate_optimal_chunk_size(total_items=10000, avg_item_memory_mb=1.0)

        assert monitor.min_chunk_size <= chunk_size <= monitor.max_chunk_size
        assert chunk_size <= 10000

    def test_chunk_size_respects_bounds(self):
        """Test that chunk size respects min/max bounds."""
        monitor = ResourceMonitor(min_chunk_size=100, max_chunk_size=1000)

        # Small total should still respect min
        chunk_size = monitor.calculate_optimal_chunk_size(total_items=50, avg_item_memory_mb=0.1)
        assert chunk_size == 50  # Can't exceed total

        # Large memory should respect max
        chunk_size = monitor.calculate_optimal_chunk_size(
            total_items=100000, avg_item_memory_mb=0.1
        )
        assert chunk_size <= 1000

    def test_get_resource_summary(self):
        """Test resource summary."""
        monitor = ResourceMonitor()
        summary = monitor.get_resource_summary()

        assert "ram_total_gb" in summary
        assert "ram_available_gb" in summary
        assert "cpu_percent" in summary
        assert "ram_within_limit" in summary
        assert "cpu_within_limit" in summary

    def test_estimate_memory_per_policy(self):
        """Test memory estimation."""
        monitor = ResourceMonitor()

        mem_per_policy = monitor.estimate_memory_per_policy(sample_policies=100, n_timesteps=360)

        assert mem_per_policy > 0
        assert isinstance(mem_per_policy, float)

    def test_history_tracking(self):
        """Test that snapshots are tracked in history."""
        monitor = ResourceMonitor()

        initial_count = len(monitor.history)
        monitor.get_snapshot()
        monitor.get_snapshot()

        assert len(monitor.history) == initial_count + 2

    def test_get_history_summary(self):
        """Test history summary."""
        monitor = ResourceMonitor()

        # Generate some history
        for _ in range(10):
            monitor.get_snapshot()
            time.sleep(0.01)

        summary = monitor.get_history_summary(last_n=10)

        assert "ram_mean" in summary
        assert "ram_max" in summary
        assert "cpu_mean" in summary
        assert summary["snapshot_count"] > 0


class TestMemoryProfiler:
    """Test suite for memory profiling utilities."""

    def test_get_memory_usage(self):
        """Test getting current memory usage."""
        mem = get_memory_usage()

        assert "rss_mb" in mem
        assert "vms_mb" in mem
        assert "percent" in mem
        assert mem["rss_mb"] > 0

    def test_profile_memory_decorator(self):
        """Test memory profiling decorator."""

        @profile_memory
        def allocate_memory():
            # Allocate some memory
            data = [0] * 1000000
            return len(data)

        result = allocate_memory()
        assert result == 1000000

    def test_memory_tracker_context_manager(self):
        """Test MemoryTracker context manager."""
        with MemoryTracker("Test operation", log_results=False) as tracker:
            # Allocate some memory
            data = [0] * 1000000

        assert tracker.peak_mb > 0
        assert tracker.elapsed > 0

        summary = tracker.get_summary()
        assert "peak_mb" in summary
        assert "elapsed_seconds" in summary

    def test_estimate_dataframe_memory(self):
        """Test DataFrame memory estimation."""
        df = pd.DataFrame({"a": range(1000), "b": range(1000), "c": ["test"] * 1000})

        mem_mb = estimate_dataframe_memory(df)
        assert mem_mb > 0


class TestDistributedExecutor:
    """Test suite for DistributedExecutor."""

    @pytest.fixture
    def sample_data(self):
        """Create sample data for testing."""
        return pd.DataFrame({"id": range(1000), "value": np.random.randn(1000)})

    @pytest.fixture
    def temp_checkpoint_dir(self):
        """Create temporary checkpoint directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_initialization(self, temp_checkpoint_dir):
        """Test executor initialization."""
        config = DistributedConfig(checkpoint_dir=temp_checkpoint_dir, max_workers=2)
        executor = DistributedExecutor(config)

        assert executor.max_workers == 2
        assert executor.checkpoint_dir is not None

    def test_create_chunks(self, sample_data):
        """Test chunk creation."""
        config = DistributedConfig()
        executor = DistributedExecutor(config)

        chunks = executor._create_chunks(sample_data, chunk_size=100)

        assert len(chunks) == 10
        assert chunks[0] == (0, 100)
        assert chunks[-1] == (900, 1000)

    def test_simple_execution(self, sample_data):
        """Test simple distributed execution."""
        config = DistributedConfig(
            chunk_size_auto=False, chunk_size_manual=200, max_workers=2, verbose=False
        )
        executor = DistributedExecutor(config)

        def process_chunk(chunk_df):
            return chunk_df["value"].sum()

        result = executor.execute(data=sample_data, process_func=process_chunk, resume=False)

        assert isinstance(result, ExecutionResult)
        assert result.chunks_completed > 0
        assert result.total_items == len(sample_data)
        assert len(result.results) == result.chunks_completed

    def test_execution_with_checkpoint(self, sample_data, temp_checkpoint_dir):
        """Test execution with checkpointing."""
        config = DistributedConfig(
            chunk_size_auto=False,
            chunk_size_manual=200,
            checkpoint_dir=temp_checkpoint_dir,
            save_intermediate=True,
            max_workers=2,
            verbose=False,
        )
        executor = DistributedExecutor(config)

        def process_chunk(chunk_df):
            return chunk_df["value"].sum()

        result = executor.execute(data=sample_data, process_func=process_chunk, resume=False)

        assert result.chunks_completed > 0

        # Check that checkpoint was saved
        checkpoint_path = Path(temp_checkpoint_dir) / "checkpoint.json"
        assert checkpoint_path.exists()

        # Check that intermediate results were saved
        result_files = list(Path(temp_checkpoint_dir).glob("chunk_*.pkl"))
        assert len(result_files) > 0

    def test_resume_from_checkpoint(self, sample_data, temp_checkpoint_dir):
        """Test resuming from checkpoint."""
        config = DistributedConfig(
            chunk_size_auto=False,
            chunk_size_manual=200,
            checkpoint_dir=temp_checkpoint_dir,
            save_intermediate=True,
            max_workers=2,
            verbose=False,
        )
        executor = DistributedExecutor(config)

        def process_chunk(chunk_df):
            return chunk_df["value"].sum()

        # First execution
        result1 = executor.execute(data=sample_data, process_func=process_chunk, resume=False)

        # Second execution (should resume)
        result2 = executor.execute(data=sample_data, process_func=process_chunk, resume=True)

        # Should complete immediately since all chunks done
        assert result2.chunks_completed == 0  # No new chunks processed

    def test_auto_chunk_sizing(self, sample_data):
        """Test automatic chunk sizing."""
        config = DistributedConfig(chunk_size_auto=True, max_workers=2, verbose=False)
        executor = DistributedExecutor(config)

        def process_chunk(chunk_df):
            return len(chunk_df)

        result = executor.execute(
            data=sample_data, process_func=process_chunk, avg_item_memory_mb=1.0, resume=False
        )

        assert result.chunks_completed > 0
        assert sum(result.results) == len(sample_data)

    def test_execution_report(self, sample_data):
        """Test execution report generation."""
        config = DistributedConfig(
            chunk_size_auto=False, chunk_size_manual=200, max_workers=2, verbose=False
        )
        executor = DistributedExecutor(config)

        def process_chunk(chunk_df):
            return len(chunk_df)

        result = executor.execute(data=sample_data, process_func=process_chunk, resume=False)

        report = executor.get_execution_report(result)

        assert isinstance(report, str)
        assert "DISTRIBUTED EXECUTION REPORT" in report
        assert "Total items:" in report

    def test_clear_checkpoint(self, temp_checkpoint_dir):
        """Test checkpoint clearing."""
        config = DistributedConfig(checkpoint_dir=temp_checkpoint_dir)
        executor = DistributedExecutor(config)

        # Create some checkpoint files
        checkpoint_path = Path(temp_checkpoint_dir) / "checkpoint.json"
        checkpoint_path.write_text('{"test": "data"}')

        result_path = Path(temp_checkpoint_dir) / "chunk_0000.pkl"
        result_path.write_bytes(b"test")

        # Clear checkpoint
        executor.clear_checkpoint()

        # Verify files removed
        assert not checkpoint_path.exists()
        assert not result_path.exists()


class TestChunkStatus:
    """Test suite for ChunkStatus."""

    def test_chunk_status_creation(self):
        """Test creating ChunkStatus."""
        status = ChunkStatus(chunk_id=0, start_idx=0, end_idx=100, status="pending")

        assert status.chunk_id == 0
        assert status.status == "pending"
        assert status.attempts == 0

    def test_chunk_status_to_dict(self):
        """Test converting ChunkStatus to dict."""
        status = ChunkStatus(
            chunk_id=0,
            start_idx=0,
            end_idx=100,
            status="completed",
            start_time=1000.0,
            end_time=1010.0,
        )

        d = status.to_dict()

        assert d["chunk_id"] == 0
        assert d["status"] == "completed"
        assert d["duration"] == 10.0


class TestExecutionResult:
    """Test suite for ExecutionResult."""

    def test_execution_result_summary(self):
        """Test execution result summary."""
        result = ExecutionResult(
            total_items=1000,
            chunks_completed=10,
            chunks_failed=0,
            total_duration=60.0,
            results=[100] * 10,
            chunk_statuses=[],
            resource_summary={},
        )

        summary = result.to_summary()

        assert summary["total_items"] == 1000
        assert summary["chunks_completed"] == 10
        assert summary["success_rate"] == 1.0
        assert summary["items_per_second"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
