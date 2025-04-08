"""
Task Queue with improved cancellation support
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Set

logger = logging.getLogger(__name__)


@dataclass
class QueueStats:
    """Statistics for the task queue."""
    queue_size: int = 0
    enqueued_total: int = 0
    dequeued_total: int = 0
    avg_wait_time: float = 0.0
    peak_queue_size: int = 0
    last_enqueued_at: Optional[float] = None
    last_dequeued_at: Optional[float] = None
    wait_times: List[float] = field(default_factory=list)
    _wait_time_window: int = 100  # Track last N wait times

    def update_enqueue(self) -> None:
        """Update statistics when a task is enqueued."""
        self.enqueued_total += 1
        self.queue_size += 1
        self.peak_queue_size = max(self.peak_queue_size, self.queue_size)
        self.last_enqueued_at = time.time()

    def update_dequeue(self, wait_time: Optional[float] = None) -> None:
        """
        Update statistics when a task is dequeued.

        Args:
            wait_time: Optional wait time to record
        """
        self.dequeued_total += 1
        self.queue_size -= 1
        self.last_dequeued_at = time.time()

        # Record wait time if provided
        if wait_time is not None:
            # Maintain a sliding window of wait times
            self.wait_times.append(wait_time)
            if len(self.wait_times) > self._wait_time_window:
                self.wait_times.pop(0)

            # Update average wait time
            if self.wait_times:
                self.avg_wait_time = sum(self.wait_times) / len(self.wait_times)


class TaskQueue:
    """
    Priority-based task queue with statistics tracking.

    Provides an asyncio.PriorityQueue with additional functionality
    for monitoring and managing task queues.
    """

    def __init__(self, max_size: Optional[int] = None):
        """
        Initialize the task queue.

        Args:
            max_size: Maximum queue size (None for unlimited)
        """
        self.queue = asyncio.PriorityQueue(maxsize=max_size if max_size is not None else 0)
        self.stats = QueueStats()

        # Track task entry timestamps with task_id key
        self._task_entry_times: Dict[str, float] = {}
        # Track which task_ids are in the queue
        self._queued_task_ids: Set[str] = set()
        # Track which task_ids are cancelled but still in the queue
        self._cancelled_task_ids: Set[str] = set()
        # Lock for operations on the queue
        self._queue_lock = asyncio.Lock()

    async def put(
            self,
            priority: int,
            task_id: str,
            task_func: Any,
            args: Tuple,
            kwargs: Dict,
            timeout: Optional[float]
    ) -> None:
        """
        Put a task in the queue.

        Args:
            priority: Task priority (lower is higher priority)
            task_id: Unique task identifier
            task_func: Function to execute
            args: Positional arguments
            kwargs: Keyword arguments
            timeout: Optional task timeout
        """
        # Record entry time for wait time calculations
        entry_time = time.time()

        # Thread-safe update of tracking collections
        async with self._queue_lock:
            self._task_entry_times[task_id] = entry_time
            self._queued_task_ids.add(task_id)

            # Put the task in the queue
            queue_item = (priority, task_id, task_func, args, kwargs, timeout)
            await self.queue.put(queue_item)

            # Update statistics
            self.stats.update_enqueue()

        logger.debug(f"Task {task_id} added to queue with priority {priority}")

    async def get(self, timeout: Optional[float] = None) -> Tuple:
        """
        Get a task from the queue.

        Args:
            timeout: Optional timeout for waiting

        Returns:
            Tuple of (priority, task_id, task_func, args, kwargs, timeout)

        Raises:
            asyncio.TimeoutError: If timeout is reached before a task is available
        """
        # If timeout provided, use asyncio.timeout context manager (Python 3.11+)
        if timeout is not None:
            async with asyncio.timeout(timeout):
                return await self._get_valid_task()
        else:
            return await self._get_valid_task()

    async def _get_valid_task(self) -> Tuple[int, str, Any, tuple, dict, float | None] | None:
        """
        Get the next valid task from the queue, skipping cancelled tasks.

        Returns:
            Tuple of (priority, task_id, task_func, args, kwargs, timeout)
        """
        while True:
            item = await self.queue.get()
            # Explicitly unpack to ensure type correctness
            priority: int = item[0]
            task_id: str = item[1]
            task_func: Any = item[2]
            args: Tuple = item[3]
            kwargs: Dict = item[4]
            task_timeout: Optional[int] = item[5]

            # Check if this task was cancelled while in queue
            async with self._queue_lock:
                if task_id in self._cancelled_task_ids:
                    # Skip this task, it was cancelled
                    self._cancelled_task_ids.discard(task_id)
                    self.queue.task_done()
                    logger.debug(f"Skipping cancelled task {task_id}")
                    continue

                # Calculate and record wait time
                wait_time = None
                if task_id in self._task_entry_times:
                    entry_time = self._task_entry_times[task_id]
                    wait_time = time.time() - entry_time
                    logger.debug(f"Task {task_id} waited {wait_time:.3f}s in queue")
                    del self._task_entry_times[task_id]  # Clean up

                # Remove from queued set
                self._queued_task_ids.discard(task_id)

                # Update statistics with wait time
                self.stats.update_dequeue(wait_time)

            # Return as explicitly typed tuple
            return priority, task_id, task_func, args, kwargs, task_timeout

    def task_done(self) -> None:
        """Mark a task as done."""
        self.queue.task_done()

    async def join(self) -> None:
        """Wait for all tasks to be processed."""
        await self.queue.join()

    async def is_task_queued(self, task_id: str) -> bool:
        """
        Check if a task is currently in the queue.

        Args:
            task_id: Task identifier to check

        Returns:
            True if the task is in the queue, False otherwise
        """
        async with self._queue_lock:
            return task_id in self._queued_task_ids

    async def remove_task(self, task_id: str) -> bool:
        """
        Mark a task as cancelled so it will be skipped when dequeued.

        Args:
            task_id: Task identifier to remove

        Returns:
            True if the task was in the queue, False otherwise
        """
        async with self._queue_lock:
            if task_id not in self._queued_task_ids:
                return False

            # Mark as cancelled and update all tracking data atomically
            self._cancelled_task_ids.add(task_id)
            self._queued_task_ids.discard(task_id)

            if task_id in self._task_entry_times:
                del self._task_entry_times[task_id]

            # Update queue statistics - decrease size as this task will be skipped
            self.stats.queue_size -= 1

            logger.debug(f"Task {task_id} marked for cancellation")
            return True

    async def clean_cancelled_tasks(self, task_ids: List[str]) -> None:
        """
        Clean up tracking for a list of task IDs.
        """
        async with self._queue_lock:
            for task_id in task_ids:
                self._cancelled_task_ids.discard(task_id)
                self._queued_task_ids.discard(task_id)
                if task_id in self._task_entry_times:
                    del self._task_entry_times[task_id]

    @property
    def qsize(self) -> int:
        """Get current queue size."""
        return self.queue.qsize()

    @property
    def empty(self) -> bool:
        """Check if queue is empty."""
        return self.queue.empty()

    @property
    def full(self) -> bool:
        """Check if queue is full."""
        return self.queue.full()

    def clear_tracking(self) -> None:
        """
        Clear task tracking dictionaries.
        Ensures no memory leaks from cancelled tasks by clearing all tracking data structures.
        """
        self._task_entry_times.clear()
        self._queued_task_ids.clear()
        self._cancelled_task_ids.clear()
