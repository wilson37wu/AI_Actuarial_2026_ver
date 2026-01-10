"""
Resource monitoring for dynamic chunk sizing and system health tracking.

This module provides utilities to monitor system resources (RAM, CPU) and
calculate optimal chunk sizes for distributed processing to prevent system overload.
"""

import logging
import time
import warnings
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import psutil

logger = logging.getLogger(__name__)


@dataclass
class ResourceSnapshot:
    """Snapshot of system resources at a point in time."""

    timestamp: float
    ram_total_gb: float
    ram_available_gb: float
    ram_used_gb: float
    ram_percent: float
    cpu_percent: float
    cpu_count: int

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp,
            "ram_total_gb": self.ram_total_gb,
            "ram_available_gb": self.ram_available_gb,
            "ram_used_gb": self.ram_used_gb,
            "ram_percent": self.ram_percent,
            "cpu_percent": self.cpu_percent,
            "cpu_count": self.cpu_count,
        }


class ResourceMonitor:
    """
    Monitor system resources and calculate optimal chunk sizes.

    Features:
    - Real-time RAM and CPU monitoring
    - Configurable resource caps (default 90%)
    - Dynamic chunk size calculation
    - Resource history tracking
    - Warning system for high usage

    Example:
        >>> monitor = ResourceMonitor(max_ram_pct=0.90, max_cpu_pct=0.90)
        >>> snapshot = monitor.get_snapshot()
        >>> print(f"RAM: {snapshot.ram_percent:.1f}%, CPU: {snapshot.cpu_percent:.1f}%")
        >>>
        >>> chunk_size = monitor.calculate_optimal_chunk_size(
        ...     total_items=100000,
        ...     avg_item_memory_mb=10
        ... )
        >>> print(f"Optimal chunk size: {chunk_size}")
    """

    def __init__(
        self,
        max_ram_pct: float = 0.90,
        max_cpu_pct: float = 0.90,
        min_chunk_size: int = 100,
        max_chunk_size: int = 10000,
        safety_margin: float = 0.10,
    ):
        """
        Initialize resource monitor.

        Args:
            max_ram_pct: Maximum RAM usage percentage (0-1)
            max_cpu_pct: Maximum CPU usage percentage (0-1)
            min_chunk_size: Minimum chunk size regardless of resources
            max_chunk_size: Maximum chunk size regardless of resources
            safety_margin: Safety margin for resource calculations (0-1)
        """
        self.max_ram_pct = max_ram_pct
        self.max_cpu_pct = max_cpu_pct
        self.min_chunk_size = min_chunk_size
        self.max_chunk_size = max_chunk_size
        self.safety_margin = safety_margin

        self.history = []
        self._last_cpu_check = 0
        self._cpu_check_interval = 1.0  # seconds

        # Validate psutil is available
        if not hasattr(psutil, "virtual_memory"):
            raise ImportError("psutil not properly installed")

        logger.info(
            f"ResourceMonitor initialized: "
            f"max_ram={max_ram_pct * 100:.0f}%, max_cpu={max_cpu_pct * 100:.0f}%"
        )

    def get_snapshot(self) -> ResourceSnapshot:
        """
        Get current resource snapshot.

        Returns:
            ResourceSnapshot with current system state
        """
        # Get memory info
        mem = psutil.virtual_memory()

        # Get CPU info (with caching to avoid excessive calls)
        current_time = time.time()
        if current_time - self._last_cpu_check > self._cpu_check_interval:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            self._last_cpu_check = current_time
        else:
            cpu_percent = psutil.cpu_percent(interval=0)

        snapshot = ResourceSnapshot(
            timestamp=current_time,
            ram_total_gb=mem.total / (1024**3),
            ram_available_gb=mem.available / (1024**3),
            ram_used_gb=mem.used / (1024**3),
            ram_percent=mem.percent,
            cpu_percent=cpu_percent,
            cpu_count=psutil.cpu_count(),
        )

        # Add to history
        self.history.append(snapshot)

        # Keep only last 1000 snapshots
        if len(self.history) > 1000:
            self.history = self.history[-1000:]

        return snapshot

    def get_available_ram_gb(self) -> float:
        """
        Get available RAM in GB.

        Returns:
            Available RAM in GB
        """
        mem = psutil.virtual_memory()
        return mem.available / (1024**3)

    def get_ram_usage_pct(self) -> float:
        """
        Get current RAM usage percentage.

        Returns:
            RAM usage percentage (0-100)
        """
        mem = psutil.virtual_memory()
        return mem.percent

    def get_cpu_usage_pct(self) -> float:
        """
        Get current CPU usage percentage.

        Returns:
            CPU usage percentage (0-100)
        """
        return psutil.cpu_percent(interval=0.1)

    def is_ram_available(self, required_gb: float) -> bool:
        """
        Check if required RAM is available.

        Args:
            required_gb: Required RAM in GB

        Returns:
            True if RAM is available
        """
        available = self.get_available_ram_gb()
        return available >= required_gb

    def is_within_limits(self) -> Tuple[bool, str]:
        """
        Check if current resource usage is within configured limits.

        Returns:
            Tuple of (is_ok, message)
        """
        snapshot = self.get_snapshot()

        ram_ok = snapshot.ram_percent <= (self.max_ram_pct * 100)
        cpu_ok = snapshot.cpu_percent <= (self.max_cpu_pct * 100)

        if not ram_ok and not cpu_ok:
            return (
                False,
                f"RAM {snapshot.ram_percent:.1f}% and CPU {snapshot.cpu_percent:.1f}% exceed limits",
            )
        elif not ram_ok:
            return (
                False,
                f"RAM {snapshot.ram_percent:.1f}% exceeds limit of {self.max_ram_pct * 100:.0f}%",
            )
        elif not cpu_ok:
            return (
                False,
                f"CPU {snapshot.cpu_percent:.1f}% exceeds limit of {self.max_cpu_pct * 100:.0f}%",
            )
        else:
            return True, "Resources within limits"

    def calculate_optimal_chunk_size(
        self,
        total_items: int,
        avg_item_memory_mb: float,
        target_ram_usage_pct: Optional[float] = None,
    ) -> int:
        """
        Calculate optimal chunk size based on available resources.

        Strategy:
        1. Get available RAM
        2. Apply safety margin
        3. Calculate how many items fit in available RAM
        4. Apply min/max bounds
        5. Adjust based on CPU availability

        Args:
            total_items: Total number of items to process
            avg_item_memory_mb: Average memory per item in MB
            target_ram_usage_pct: Target RAM usage (overrides max_ram_pct)

        Returns:
            Optimal chunk size
        """
        if target_ram_usage_pct is None:
            target_ram_usage_pct = self.max_ram_pct

        # Get current resource state
        snapshot = self.get_snapshot()

        # Calculate available RAM with safety margin
        available_ram_gb = snapshot.ram_available_gb * (1 - self.safety_margin)
        available_ram_mb = available_ram_gb * 1024

        # Calculate how many items fit in available RAM
        if avg_item_memory_mb > 0:
            items_in_ram = int(available_ram_mb / avg_item_memory_mb)
        else:
            # If memory per item unknown, use conservative estimate
            items_in_ram = self.max_chunk_size

        # Apply bounds
        chunk_size = max(self.min_chunk_size, min(items_in_ram, self.max_chunk_size))

        # Adjust based on CPU availability
        cpu_factor = 1.0
        if snapshot.cpu_percent > 80:
            cpu_factor = 0.5  # Reduce chunk size if CPU is busy
        elif snapshot.cpu_percent > 60:
            cpu_factor = 0.75

        chunk_size = int(chunk_size * cpu_factor)

        # Ensure at least min_chunk_size
        chunk_size = max(self.min_chunk_size, chunk_size)

        # Don't exceed total items
        chunk_size = min(chunk_size, total_items)

        logger.info(
            f"Calculated chunk size: {chunk_size:,} "
            f"(RAM: {snapshot.ram_percent:.1f}%, CPU: {snapshot.cpu_percent:.1f}%, "
            f"Available RAM: {available_ram_gb:.2f}GB)"
        )

        return chunk_size

    def wait_for_resources(
        self,
        required_ram_gb: Optional[float] = None,
        max_wait_seconds: float = 300,
        check_interval: float = 5.0,
    ) -> bool:
        """
        Wait until resources are available.

        Args:
            required_ram_gb: Required RAM in GB (None = just check limits)
            max_wait_seconds: Maximum time to wait
            check_interval: Time between checks in seconds

        Returns:
            True if resources became available, False if timeout
        """
        start_time = time.time()

        while time.time() - start_time < max_wait_seconds:
            # Check if within limits
            is_ok, message = self.is_within_limits()

            # Check RAM if specified
            ram_ok = True
            if required_ram_gb is not None:
                ram_ok = self.is_ram_available(required_ram_gb)

            if is_ok and ram_ok:
                return True

            # Log waiting status
            if not is_ok:
                logger.warning(f"Waiting for resources: {message}")
            if not ram_ok:
                logger.warning(
                    f"Waiting for {required_ram_gb:.2f}GB RAM "
                    f"(available: {self.get_available_ram_gb():.2f}GB)"
                )

            time.sleep(check_interval)

        logger.error(f"Timeout waiting for resources after {max_wait_seconds}s")
        return False

    def get_resource_summary(self) -> Dict:
        """
        Get summary of current resources.

        Returns:
            Dictionary with resource summary
        """
        snapshot = self.get_snapshot()

        return {
            "ram_total_gb": snapshot.ram_total_gb,
            "ram_available_gb": snapshot.ram_available_gb,
            "ram_used_gb": snapshot.ram_used_gb,
            "ram_percent": snapshot.ram_percent,
            "ram_within_limit": snapshot.ram_percent <= (self.max_ram_pct * 100),
            "cpu_percent": snapshot.cpu_percent,
            "cpu_count": snapshot.cpu_count,
            "cpu_within_limit": snapshot.cpu_percent <= (self.max_cpu_pct * 100),
            "max_ram_pct": self.max_ram_pct * 100,
            "max_cpu_pct": self.max_cpu_pct * 100,
        }

    def estimate_memory_per_policy(
        self, sample_policies: int = 100, n_timesteps: int = 360
    ) -> float:
        """
        Estimate memory usage per policy (rough heuristic).

        Args:
            sample_policies: Number of policies to estimate for
            n_timesteps: Number of timesteps in projection

        Returns:
            Estimated memory per policy in MB
        """
        # Rough heuristic based on typical policy projection
        # Each policy state: ~1KB
        # Each cashflow record: ~500 bytes
        # Total per policy per timestep: ~1.5KB
        # Plus overhead: 2x

        bytes_per_policy_per_timestep = 1500 * 2
        total_bytes = bytes_per_policy_per_timestep * n_timesteps
        mb_per_policy = total_bytes / (1024**2)

        return mb_per_policy

    def check_and_warn(self) -> None:
        """Check resources and issue warnings if limits approached."""
        snapshot = self.get_snapshot()

        # Warn if approaching RAM limit
        if snapshot.ram_percent > (self.max_ram_pct * 100 * 0.9):
            warnings.warn(
                f"RAM usage {snapshot.ram_percent:.1f}% approaching limit "
                f"of {self.max_ram_pct * 100:.0f}%",
                ResourceWarning,
            )

        # Warn if approaching CPU limit
        if snapshot.cpu_percent > (self.max_cpu_pct * 100 * 0.9):
            warnings.warn(
                f"CPU usage {snapshot.cpu_percent:.1f}% approaching limit "
                f"of {self.max_cpu_pct * 100:.0f}%",
                ResourceWarning,
            )

    def get_history_summary(self, last_n: int = 100) -> Dict:
        """
        Get summary statistics from resource history.

        Args:
            last_n: Number of recent snapshots to summarize

        Returns:
            Dictionary with summary statistics
        """
        if not self.history:
            return {}

        recent = self.history[-last_n:]

        ram_percents = [s.ram_percent for s in recent]
        cpu_percents = [s.cpu_percent for s in recent]

        return {
            "ram_mean": sum(ram_percents) / len(ram_percents),
            "ram_max": max(ram_percents),
            "ram_min": min(ram_percents),
            "cpu_mean": sum(cpu_percents) / len(cpu_percents),
            "cpu_max": max(cpu_percents),
            "cpu_min": min(cpu_percents),
            "snapshot_count": len(recent),
        }
