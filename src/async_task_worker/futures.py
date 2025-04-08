"""
Task Futures Module

This module provides functionality for tracking task completion
through asyncio futures.
"""

import asyncio
import logging
import weakref
from typing import Self, Set

from async_task_worker.exceptions import TaskCancellationError, TaskExecutionError
from async_task_worker.status import TaskInfo, TaskStatus

logger = logging.getLogger(__name__)


class TaskFutureManager:
    """
    Manages futures for task completion.

    Uses standard asyncio primitives to track task completion with
    proper memory management through weak references.
    """

    def __init__(self):
        """Initialize the task future manager."""
        # Only store futures in the weak dictionary to prevent memory leaks
        self._futures = weakref.WeakValueDictionary()

        # Keep a set of task_ids with pending futures for more efficient iteration
        self._pending_tasks: Set[str] = set()

        # Lock for synchronization
        self._futures_lock = asyncio.Lock()

        # Cleanup interval in seconds
        self._cleanup_interval = 60.0
        self._cleanup_task = None

    async def __aenter__(self) -> Self:
        """Support async context manager protocol."""
        self._start_cleanup_task()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Support async context manager protocol."""
        await self.cancel_all_futures()
        self._stop_cleanup_task()

    def _start_cleanup_task(self) -> None:
        """Start the periodic cleanup task if not already running."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
            self._cleanup_task.set_name("future_manager_cleanup")

    def _stop_cleanup_task(self) -> None:
        """Stop the periodic cleanup task if running."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()

    async def _periodic_cleanup(self) -> None:
        """Periodically clean up completed or lost futures."""
        try:
            while True:
                await asyncio.sleep(self._cleanup_interval)
                await self._cleanup_completed_futures()
        except asyncio.CancelledError:
            logger.debug("Future manager cleanup task cancelled")
        except Exception as e:
            logger.error(f"Error in future manager cleanup task: {e}", exc_info=True)

    async def _cleanup_completed_futures(self) -> None:
        """Clean up completed futures from the pending tasks set."""
        async with self._futures_lock:
            to_remove = set()
            for task_id in self._pending_tasks:
                # Check if the future exists and is done or has been garbage collected
                future = self._futures.get(task_id)
                if future is None or future.done():
                    to_remove.add(task_id)

            # Remove completed tasks from the pending set
            self._pending_tasks -= to_remove

            count = len(to_remove)
            if count > 0:
                logger.debug(f"Cleaned up {count} completed task futures")

    async def get_future(self, task_id: str) -> asyncio.Future:
        """
        Get a future for a task that will resolve when the task completes.

        Args:
            task_id: Task identifier

        Returns:
            Future that will resolve to the task result
        """
        async with self._futures_lock:
            # Return existing future if we have one
            if task_id in self._futures:
                return self._futures[task_id]

            # Create a new future
            future = asyncio.Future()
            self._futures[task_id] = future
            self._pending_tasks.add(task_id)

            # Ensure cleanup task is running
            self._start_cleanup_task()

            return future

    async def set_result(self, task_id: str, result) -> None:
        """
        Set the result of a task future.

        Args:
            task_id: Task identifier
            result: Result to set
        """
        async with self._futures_lock:
            future = self._futures.get(task_id)
            if future and not future.done():
                future.set_result(result)
                # Remove from pending tasks immediately
                self._pending_tasks.discard(task_id)

    async def set_exception(self, task_id: str, exception: Exception) -> None:
        """
        Set an exception on a task future.

        Args:
            task_id: Task identifier
            exception: Exception to set
        """
        async with self._futures_lock:
            future = self._futures.get(task_id)
            if future and not future.done():
                future.set_exception(exception)
                # Remove from pending tasks immediately
                self._pending_tasks.discard(task_id)

    async def complete_from_result(self, task_id: str, result: any) -> None:
        """
        Complete a future with a result directly.

        Args:
            task_id: Task identifier
            result: The result to set
        """
        await self.set_result(task_id, result)

    async def complete_from_task_info(self, task_id: str, task_info: TaskInfo) -> None:
        """
        Complete a future based on the state of a TaskInfo object.

        Args:
            task_id: Task identifier
            task_info: Task information
        """
        # First check the task state without acquiring a lock
        if not task_info.is_terminal_state():
            return

        # Then choose the appropriate action based on state
        if task_info.status == TaskStatus.COMPLETED:
            await self.set_result(task_id, task_info.result)
        elif task_info.status == TaskStatus.FAILED:
            await self.set_exception(
                task_id,
                TaskExecutionError(task_info.error or "Unknown error", task_id=task_id)
            )
        elif task_info.status == TaskStatus.CANCELLED:
            await self.set_exception(
                task_id,
                TaskCancellationError(task_info.error or "Task cancelled", task_id=task_id)
            )

    async def cancel_future(self, task_id: str) -> bool:
        """
        Cancel a task future.

        Args:
            task_id: Task identifier

        Returns:
            True if the future was cancelled, False if not found or already done
        """
        async with self._futures_lock:
            future = self._futures.get(task_id)
            if future and not future.done():
                future.cancel()
                # Remove from pending tasks immediately
                self._pending_tasks.discard(task_id)
                return True
            return False

    async def cancel_all_futures(self) -> int:
        """
        Cancel all pending futures.

        Returns:
            Number of futures cancelled
        """
        cancelled_count = 0
        async with self._futures_lock:
            # Create a list of task_ids from pending_tasks to avoid modifying during iteration
            task_ids = list(self._pending_tasks)

            for task_id in task_ids:
                future = self._futures.get(task_id)
                if future and not future.done():
                    future.cancel()
                    cancelled_count += 1

            # Clear the pending tasks
            self._pending_tasks.clear()

        return cancelled_count

    async def get_pending_count(self) -> int:
        """
        Get the number of pending futures.

        Returns:
            Number of pending futures
        """
        async with self._futures_lock:
            return len(self._pending_tasks)
