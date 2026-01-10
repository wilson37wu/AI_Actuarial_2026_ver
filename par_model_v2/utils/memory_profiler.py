"""
Memory profiling utilities for tracking memory usage.

This module provides decorators and utilities to profile memory usage
of functions and track memory consumption during execution.
"""

import functools
import logging
import os
import time
import tracemalloc
from typing import Any, Callable, Dict, Optional

import psutil

logger = logging.getLogger(__name__)


def get_memory_usage() -> Dict[str, float]:
    """
    Get current process memory usage.

    Returns:
        Dictionary with memory usage in MB
    """
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()

    return {
        "rss_mb": mem_info.rss / (1024**2),  # Resident Set Size
        "vms_mb": mem_info.vms / (1024**2),  # Virtual Memory Size
        "percent": process.memory_percent(),
    }


def profile_memory(func: Callable) -> Callable:
    """
    Decorator to profile memory usage of a function.

    Tracks peak memory usage and reports it after function execution.

    Example:
        >>> @profile_memory
        ... def process_data(data):
        ...     result = expensive_operation(data)
        ...     return result
        >>>
        >>> result = process_data(my_data)
        # Logs: process_data: Peak memory: 123.45 MB

    Args:
        func: Function to profile

    Returns:
        Wrapped function with memory profiling
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Start tracking
        tracemalloc.start()
        start_mem = get_memory_usage()
        start_time = time.time()

        try:
            # Execute function
            result = func(*args, **kwargs)

            # Get peak memory
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

            # Get end memory
            end_mem = get_memory_usage()
            elapsed = time.time() - start_time

            # Log results
            logger.info(
                f"{func.__name__}: "
                f"Peak memory: {peak / (1024**2):.2f} MB, "
                f"Delta RSS: {end_mem['rss_mb'] - start_mem['rss_mb']:.2f} MB, "
                f"Time: {elapsed:.2f}s"
            )

            return result

        except Exception as e:
            tracemalloc.stop()
            logger.error(f"{func.__name__}: Error during profiling: {e}")
            raise

    return wrapper


class MemoryTracker:
    """
    Context manager for tracking memory usage within a code block.

    Example:
        >>> with MemoryTracker("Data processing") as tracker:
        ...     process_large_dataset()
        >>>
        >>> print(f"Peak memory: {tracker.peak_mb:.2f} MB")
    """

    def __init__(self, name: str = "Operation", log_results: bool = True):
        """
        Initialize memory tracker.

        Args:
            name: Name of the operation being tracked
            log_results: Whether to log results automatically
        """
        self.name = name
        self.log_results = log_results
        self.start_mem = None
        self.end_mem = None
        self.peak_mb = 0
        self.current_mb = 0
        self.delta_mb = 0
        self.start_time = None
        self.elapsed = 0

    def __enter__(self):
        """Start tracking."""
        tracemalloc.start()
        self.start_mem = get_memory_usage()
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop tracking and log results."""
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        self.end_mem = get_memory_usage()
        self.peak_mb = peak / (1024**2)
        self.current_mb = current / (1024**2)
        self.delta_mb = self.end_mem["rss_mb"] - self.start_mem["rss_mb"]
        self.elapsed = time.time() - self.start_time

        if self.log_results:
            logger.info(
                f"{self.name}: "
                f"Peak: {self.peak_mb:.2f} MB, "
                f"Delta RSS: {self.delta_mb:.2f} MB, "
                f"Time: {self.elapsed:.2f}s"
            )

    def get_summary(self) -> Dict[str, float]:
        """
        Get summary of tracked memory usage.

        Returns:
            Dictionary with memory statistics
        """
        return {
            "peak_mb": self.peak_mb,
            "current_mb": self.current_mb,
            "delta_mb": self.delta_mb,
            "start_rss_mb": self.start_mem["rss_mb"] if self.start_mem else 0,
            "end_rss_mb": self.end_mem["rss_mb"] if self.end_mem else 0,
            "elapsed_seconds": self.elapsed,
        }


class MemoryMonitor:
    """
    Continuous memory monitoring for long-running operations.

    Example:
        >>> monitor = MemoryMonitor(interval=5.0, threshold_mb=1000)
        >>> monitor.start()
        >>> # ... long running operation ...
        >>> monitor.stop()
        >>> print(monitor.get_report())
    """

    def __init__(
        self,
        interval: float = 5.0,
        threshold_mb: Optional[float] = None,
        callback: Optional[Callable] = None,
    ):
        """
        Initialize memory monitor.

        Args:
            interval: Monitoring interval in seconds
            threshold_mb: Memory threshold in MB (triggers callback)
            callback: Function to call when threshold exceeded
        """
        self.interval = interval
        self.threshold_mb = threshold_mb
        self.callback = callback
        self.snapshots = []
        self.is_running = False
        self._thread = None

    def start(self):
        """Start monitoring in background thread."""
        import threading

        self.is_running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info(f"Memory monitoring started (interval: {self.interval}s)")

    def stop(self):
        """Stop monitoring."""
        self.is_running = False
        if self._thread:
            self._thread.join(timeout=self.interval * 2)
        logger.info("Memory monitoring stopped")

    def _monitor_loop(self):
        """Background monitoring loop."""
        while self.is_running:
            mem = get_memory_usage()
            mem["timestamp"] = time.time()
            self.snapshots.append(mem)

            # Check threshold
            if self.threshold_mb and mem["rss_mb"] > self.threshold_mb:
                logger.warning(
                    f"Memory threshold exceeded: {mem['rss_mb']:.2f} MB > "
                    f"{self.threshold_mb:.2f} MB"
                )
                if self.callback:
                    try:
                        self.callback(mem)
                    except Exception as e:
                        logger.error(f"Callback error: {e}")

            time.sleep(self.interval)

    def get_report(self) -> Dict[str, Any]:
        """
        Get monitoring report.

        Returns:
            Dictionary with monitoring statistics
        """
        if not self.snapshots:
            return {}

        rss_values = [s["rss_mb"] for s in self.snapshots]

        return {
            "snapshot_count": len(self.snapshots),
            "duration_seconds": self.snapshots[-1]["timestamp"] - self.snapshots[0]["timestamp"],
            "rss_mean_mb": sum(rss_values) / len(rss_values),
            "rss_max_mb": max(rss_values),
            "rss_min_mb": min(rss_values),
            "rss_final_mb": rss_values[-1],
            "threshold_mb": self.threshold_mb,
            "threshold_exceeded": any(s["rss_mb"] > self.threshold_mb for s in self.snapshots)
            if self.threshold_mb
            else False,
        }

    def clear_snapshots(self):
        """Clear snapshot history."""
        self.snapshots.clear()


def estimate_dataframe_memory(df) -> float:
    """
    Estimate memory usage of a pandas DataFrame.

    Args:
        df: pandas DataFrame

    Returns:
        Memory usage in MB
    """
    import pandas as pd

    if not isinstance(df, pd.DataFrame):
        return 0.0

    return df.memory_usage(deep=True).sum() / (1024**2)


def log_memory_usage(message: str = "Current memory"):
    """
    Log current memory usage.

    Args:
        message: Message to include in log
    """
    mem = get_memory_usage()
    logger.info(
        f"{message}: RSS={mem['rss_mb']:.2f} MB, "
        f"VMS={mem['vms_mb']:.2f} MB, "
        f"Percent={mem['percent']:.1f}%"
    )


def check_memory_available(required_mb: float) -> bool:
    """
    Check if required memory is available.

    Args:
        required_mb: Required memory in MB

    Returns:
        True if memory is available
    """
    mem = psutil.virtual_memory()
    available_mb = mem.available / (1024**2)
    return available_mb >= required_mb


class MemoryBudget:
    """
    Memory budget manager for controlling memory usage.

    Example:
        >>> budget = MemoryBudget(max_mb=1000)
        >>> with budget.allocate(100, "Processing chunk 1"):
        ...     process_chunk()
    """

    def __init__(self, max_mb: float):
        """
        Initialize memory budget.

        Args:
            max_mb: Maximum memory budget in MB
        """
        self.max_mb = max_mb
        self.allocated_mb = 0
        self.allocations = {}

    def allocate(self, mb: float, name: str):
        """
        Allocate memory budget.

        Args:
            mb: Memory to allocate in MB
            name: Name of allocation

        Returns:
            Context manager
        """
        return _MemoryAllocation(self, mb, name)

    def _add_allocation(self, mb: float, name: str):
        """Add allocation."""
        if self.allocated_mb + mb > self.max_mb:
            raise MemoryError(
                f"Cannot allocate {mb:.2f} MB (would exceed budget of {self.max_mb:.2f} MB)"
            )
        self.allocated_mb += mb
        self.allocations[name] = mb
        logger.debug(
            f"Allocated {mb:.2f} MB for '{name}' (total: {self.allocated_mb:.2f}/{self.max_mb:.2f} MB)"
        )

    def _remove_allocation(self, mb: float, name: str):
        """Remove allocation."""
        self.allocated_mb -= mb
        if name in self.allocations:
            del self.allocations[name]
        logger.debug(
            f"Freed {mb:.2f} MB from '{name}' (total: {self.allocated_mb:.2f}/{self.max_mb:.2f} MB)"
        )

    def get_available(self) -> float:
        """Get available memory in MB."""
        return self.max_mb - self.allocated_mb

    def get_summary(self) -> Dict[str, Any]:
        """Get budget summary."""
        return {
            "max_mb": self.max_mb,
            "allocated_mb": self.allocated_mb,
            "available_mb": self.get_available(),
            "utilization_pct": (self.allocated_mb / self.max_mb) * 100,
            "allocations": self.allocations.copy(),
        }


class _MemoryAllocation:
    """Context manager for memory allocation."""

    def __init__(self, budget: MemoryBudget, mb: float, name: str):
        self.budget = budget
        self.mb = mb
        self.name = name

    def __enter__(self):
        self.budget._add_allocation(self.mb, self.name)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.budget._remove_allocation(self.mb, self.name)
