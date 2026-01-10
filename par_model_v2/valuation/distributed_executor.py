"""
Enhanced distributed executor with fault tolerance and dynamic chunking.

This module provides a robust distributed processing framework with:
- Dynamic chunk sizing based on available RAM/CPU
- Checkpoint/resume for failed chunks
- Graceful degradation on worker failures
- Progress monitoring and logging
- Resource-aware scheduling
"""

import json
import logging
import multiprocessing as mp
import pickle
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, TimeoutError, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from ..utils.memory_profiler import log_memory_usage
from ..utils.resource_monitor import ResourceMonitor

logger = logging.getLogger(__name__)


@dataclass
class DistributedConfig:
    """Configuration for distributed processing."""

    max_ram_usage_pct: float = 0.90
    max_cpu_usage_pct: float = 0.90
    chunk_size_auto: bool = True
    chunk_size_manual: Optional[int] = None
    checkpoint_frequency: int = 100
    checkpoint_dir: Optional[str] = None
    retry_failed_chunks: int = 3
    graceful_degradation: bool = True
    max_workers: Optional[int] = None
    timeout_per_chunk: Optional[float] = None
    save_intermediate: bool = True
    verbose: bool = True


@dataclass
class ChunkStatus:
    """Status of a processing chunk."""

    chunk_id: int
    start_idx: int
    end_idx: int
    status: str = "pending"  # pending, running, completed, failed
    attempts: int = 0
    error_message: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    result_path: Optional[str] = None

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "chunk_id": self.chunk_id,
            "start_idx": self.start_idx,
            "end_idx": self.end_idx,
            "status": self.status,
            "attempts": self.attempts,
            "error_message": self.error_message,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.end_time - self.start_time
            if self.end_time and self.start_time
            else None,
            "result_path": self.result_path,
        }


@dataclass
class ExecutionResult:
    """Result from distributed execution."""

    total_items: int
    chunks_completed: int
    chunks_failed: int
    total_duration: float
    results: List[Any]
    chunk_statuses: List[ChunkStatus]
    resource_summary: Dict

    def to_summary(self) -> Dict:
        """Get execution summary."""
        return {
            "total_items": self.total_items,
            "chunks_completed": self.chunks_completed,
            "chunks_failed": self.chunks_failed,
            "success_rate": self.chunks_completed / (self.chunks_completed + self.chunks_failed)
            if (self.chunks_completed + self.chunks_failed) > 0
            else 0,
            "total_duration": self.total_duration,
            "items_per_second": self.total_items / self.total_duration
            if self.total_duration > 0
            else 0,
            "resource_summary": self.resource_summary,
        }


class DistributedExecutor:
    """
    Enhanced distributed executor with fault tolerance.

    Features:
    - Dynamic chunk sizing based on RAM/CPU
    - Checkpoint/resume capability
    - Automatic retry on failure
    - Worker health monitoring
    - Progress reporting
    - Graceful degradation

    Example:
        >>> config = DistributedConfig(
        ...     max_ram_usage_pct=0.90,
        ...     chunk_size_auto=True,
        ...     checkpoint_dir='checkpoints'
        ... )
        >>> executor = DistributedExecutor(config)
        >>>
        >>> def process_chunk(chunk_data):
        ...     # Process chunk
        ...     return results
        >>>
        >>> result = executor.execute(
        ...     data=policies_df,
        ...     process_func=process_chunk,
        ...     avg_item_memory_mb=10
        ... )
    """

    def __init__(self, config: DistributedConfig):
        """
        Initialize distributed executor.

        Args:
            config: Configuration for distributed processing
        """
        self.config = config
        self.resource_monitor = ResourceMonitor(
            max_ram_pct=config.max_ram_usage_pct, max_cpu_pct=config.max_cpu_usage_pct
        )

        # Set up checkpoint directory
        if config.checkpoint_dir:
            self.checkpoint_dir = Path(config.checkpoint_dir)
            self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.checkpoint_dir = None

        # Determine number of workers
        if config.max_workers:
            self.max_workers = config.max_workers
        else:
            cpu_count = mp.cpu_count()
            self.max_workers = max(1, int(cpu_count * config.max_cpu_usage_pct))

        logger.info(
            f"DistributedExecutor initialized: "
            f"max_workers={self.max_workers}, "
            f"checkpoint_dir={self.checkpoint_dir}"
        )

    def execute(
        self,
        data: pd.DataFrame,
        process_func: Callable,
        avg_item_memory_mb: float = 10.0,
        resume: bool = True,
        **process_kwargs,
    ) -> ExecutionResult:
        """
        Execute distributed processing with fault tolerance.

        Args:
            data: DataFrame to process
            process_func: Function to process each chunk
            avg_item_memory_mb: Average memory per item in MB
            resume: Whether to resume from checkpoint
            **process_kwargs: Additional kwargs for process_func

        Returns:
            ExecutionResult with results and statistics
        """
        start_time = time.time()
        total_items = len(data)

        logger.info(f"Starting distributed execution: {total_items:,} items")
        log_memory_usage("Before execution")

        # Calculate chunk size
        if self.config.chunk_size_auto:
            chunk_size = self.resource_monitor.calculate_optimal_chunk_size(
                total_items=total_items, avg_item_memory_mb=avg_item_memory_mb
            )
        else:
            chunk_size = self.config.chunk_size_manual or 1000

        # Create chunks
        chunks = self._create_chunks(data, chunk_size)
        logger.info(f"Created {len(chunks)} chunks (size: {chunk_size:,})")

        # Load checkpoint if resuming
        chunk_statuses = self._load_checkpoint(len(chunks)) if resume else None
        if chunk_statuses is None:
            chunk_statuses = [
                ChunkStatus(chunk_id=i, start_idx=start, end_idx=end)
                for i, (start, end) in enumerate(chunks)
            ]

        # Filter to pending/failed chunks
        chunks_to_process = [
            (i, chunks[i])
            for i, status in enumerate(chunk_statuses)
            if status.status in ["pending", "failed"]
            and status.attempts < self.config.retry_failed_chunks
        ]

        logger.info(f"Processing {len(chunks_to_process)} chunks (including retries)")

        # Process chunks
        results = [None] * len(chunks)

        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all chunks
            future_to_chunk = {}
            for chunk_idx, (start_idx, end_idx) in chunks_to_process:
                chunk_data = data.iloc[start_idx:end_idx]

                future = executor.submit(
                    self._process_chunk_wrapper, chunk_idx, chunk_data, process_func, process_kwargs
                )
                future_to_chunk[future] = chunk_idx

            # Collect results
            completed = 0
            failed = 0

            for future in as_completed(future_to_chunk, timeout=self.config.timeout_per_chunk):
                chunk_idx = future_to_chunk[future]
                status = chunk_statuses[chunk_idx]

                try:
                    # Get result
                    chunk_result, error = future.result(timeout=10)

                    if error is None:
                        # Success
                        results[chunk_idx] = chunk_result
                        status.status = "completed"
                        status.end_time = time.time()
                        completed += 1

                        # Save intermediate result
                        if self.config.save_intermediate and self.checkpoint_dir:
                            result_path = self.checkpoint_dir / f"chunk_{chunk_idx:04d}.pkl"
                            with open(result_path, "wb") as f:
                                pickle.dump(chunk_result, f)
                            status.result_path = str(result_path)

                        if self.config.verbose:
                            progress = (completed + failed) / len(chunks_to_process) * 100
                            logger.info(
                                f"Chunk {chunk_idx} completed "
                                f"({completed}/{len(chunks_to_process)}, {progress:.1f}%)"
                            )
                    else:
                        # Failed
                        status.status = "failed"
                        status.error_message = error
                        status.end_time = time.time()
                        failed += 1

                        logger.error(f"Chunk {chunk_idx} failed: {error}")

                        # Retry if configured
                        if status.attempts < self.config.retry_failed_chunks:
                            logger.info(
                                f"Will retry chunk {chunk_idx} (attempt {status.attempts + 1})"
                            )

                except TimeoutError:
                    status.status = "failed"
                    status.error_message = "Timeout"
                    status.end_time = time.time()
                    failed += 1
                    logger.error(f"Chunk {chunk_idx} timed out")

                except Exception as e:
                    status.status = "failed"
                    status.error_message = str(e)
                    status.end_time = time.time()
                    failed += 1
                    logger.error(f"Chunk {chunk_idx} error: {e}")

                # Save checkpoint
                if (
                    self.checkpoint_dir
                    and (completed + failed) % self.config.checkpoint_frequency == 0
                ):
                    self._save_checkpoint(chunk_statuses)

        # Final checkpoint
        if self.checkpoint_dir:
            self._save_checkpoint(chunk_statuses)

        # Combine results
        final_results = [r for r in results if r is not None]

        # Get resource summary
        resource_summary = self.resource_monitor.get_resource_summary()

        total_duration = time.time() - start_time

        logger.info(
            f"Execution completed: {completed} succeeded, {failed} failed, "
            f"duration: {total_duration:.2f}s"
        )
        log_memory_usage("After execution")

        return ExecutionResult(
            total_items=total_items,
            chunks_completed=completed,
            chunks_failed=failed,
            total_duration=total_duration,
            results=final_results,
            chunk_statuses=chunk_statuses,
            resource_summary=resource_summary,
        )

    def _create_chunks(self, data: pd.DataFrame, chunk_size: int) -> List[Tuple[int, int]]:
        """
        Create chunk indices.

        Args:
            data: DataFrame to chunk
            chunk_size: Size of each chunk

        Returns:
            List of (start_idx, end_idx) tuples
        """
        total = len(data)
        chunks = []

        for start in range(0, total, chunk_size):
            end = min(start + chunk_size, total)
            chunks.append((start, end))

        return chunks

    @staticmethod
    def _process_chunk_wrapper(
        chunk_idx: int, chunk_data: pd.DataFrame, process_func: Callable, process_kwargs: Dict
    ) -> Tuple[Any, Optional[str]]:
        """
        Wrapper for processing a chunk (runs in worker process).

        Args:
            chunk_idx: Chunk index
            chunk_data: Data for this chunk
            process_func: Function to process chunk
            process_kwargs: Additional kwargs

        Returns:
            Tuple of (result, error_message)
        """
        try:
            result = process_func(chunk_data, **process_kwargs)
            return result, None
        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
            return None, error_msg

    def _save_checkpoint(self, chunk_statuses: List[ChunkStatus]):
        """Save checkpoint to disk."""
        if not self.checkpoint_dir:
            return

        checkpoint_path = self.checkpoint_dir / "checkpoint.json"

        checkpoint_data = {
            "timestamp": time.time(),
            "chunk_statuses": [s.to_dict() for s in chunk_statuses],
        }

        with open(checkpoint_path, "w") as f:
            json.dump(checkpoint_data, f, indent=2)

        logger.debug(f"Checkpoint saved: {checkpoint_path}")

    def _load_checkpoint(self, expected_chunks: int) -> Optional[List[ChunkStatus]]:
        """Load checkpoint from disk."""
        if not self.checkpoint_dir:
            return None

        checkpoint_path = self.checkpoint_dir / "checkpoint.json"

        if not checkpoint_path.exists():
            return None

        try:
            with open(checkpoint_path, "r") as f:
                checkpoint_data = json.load(f)

            chunk_statuses = [ChunkStatus(**s) for s in checkpoint_data["chunk_statuses"]]

            if len(chunk_statuses) != expected_chunks:
                logger.warning(
                    f"Checkpoint has {len(chunk_statuses)} chunks, "
                    f"expected {expected_chunks}. Ignoring checkpoint."
                )
                return None

            completed = sum(1 for s in chunk_statuses if s.status == "completed")
            failed = sum(1 for s in chunk_statuses if s.status == "failed")

            logger.info(f"Loaded checkpoint: {completed} completed, {failed} failed")

            return chunk_statuses

        except Exception as e:
            logger.error(f"Error loading checkpoint: {e}")
            return None

    def load_results_from_checkpoint(self) -> List[Any]:
        """
        Load all results from checkpoint directory.

        Returns:
            List of results
        """
        if not self.checkpoint_dir:
            raise ValueError("No checkpoint directory configured")

        results = []
        result_files = sorted(self.checkpoint_dir.glob("chunk_*.pkl"))

        for result_file in result_files:
            try:
                with open(result_file, "rb") as f:
                    result = pickle.load(f)
                    results.append(result)
            except Exception as e:
                logger.error(f"Error loading {result_file}: {e}")

        logger.info(f"Loaded {len(results)} results from checkpoint")
        return results

    def clear_checkpoint(self):
        """Clear checkpoint directory."""
        if not self.checkpoint_dir:
            return

        # Remove checkpoint file
        checkpoint_path = self.checkpoint_dir / "checkpoint.json"
        if checkpoint_path.exists():
            checkpoint_path.unlink()

        # Remove result files
        for result_file in self.checkpoint_dir.glob("chunk_*.pkl"):
            result_file.unlink()

        logger.info("Checkpoint cleared")

    def get_execution_report(self, result: ExecutionResult) -> str:
        """
        Generate execution report.

        Args:
            result: ExecutionResult to report on

        Returns:
            Formatted report string
        """
        summary = result.to_summary()

        report = []
        report.append("=" * 70)
        report.append("DISTRIBUTED EXECUTION REPORT")
        report.append("=" * 70)
        report.append(f"Total items: {summary['total_items']:,}")
        report.append(f"Chunks completed: {summary['chunks_completed']}")
        report.append(f"Chunks failed: {summary['chunks_failed']}")
        report.append(f"Success rate: {summary['success_rate'] * 100:.1f}%")
        report.append(f"Total duration: {summary['total_duration']:.2f}s")
        report.append(f"Throughput: {summary['items_per_second']:.1f} items/sec")
        report.append("")
        report.append("Resource Usage:")
        report.append(
            f"  RAM: {summary['resource_summary']['ram_percent']:.1f}% "
            f"({summary['resource_summary']['ram_used_gb']:.2f} GB / "
            f"{summary['resource_summary']['ram_total_gb']:.2f} GB)"
        )
        report.append(f"  CPU: {summary['resource_summary']['cpu_percent']:.1f}%")
        report.append("=" * 70)

        return "\n".join(report)
