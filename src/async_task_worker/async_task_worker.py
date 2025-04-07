"""
Async Task Worker

Main entry point for the task worker system that integrates all components
and provides a simple API for users.
"""

import asyncio
import logging
import uuid
import weakref
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional, Self, TypeVar, Tuple

from async_task_worker.error_handler import TaskError
from async_task_worker.task_cache import CacheAdapter, MemoryCacheAdapter, TaskCache
from async_task_worker.task_executor import TaskExecutor
from async_task_worker.task_futures import TaskFutureManager
from async_task_worker.task_queue import TaskQueue
from async_task_worker.task_status import TaskInfo, TaskStatus
from async_task_worker.worker_pool import WorkerPool

logger = logging.getLogger(__name__)

T = TypeVar('T')  # Used for generic task function return type


class AsyncTaskWorker:
    """
    Worker pool for managing asynchronous background tasks.

    Features:
    - Task queuing with priorities
    - Configurable number of worker tasks
    - Task status tracking
    - Task cancellation
    - Progress reporting
    - Result caching
    """

    def __init__(
            self,
            max_workers: int = 10,
            task_timeout: Optional[float] = None,
            worker_poll_interval: float = 1.0,
            cache_enabled: bool = False,
            cache_ttl: Optional[int] = 3600,  # 1 hour default
            cache_max_size: Optional[int] = 1000,
            cache_adapter: Optional[CacheAdapter] = None,
            max_queue_size: Optional[int] = None,
            task_retention_days: Optional[int] = 7,
            cleanup_interval: int = 3600  # Cleanup every hour by default
    ):
        """
        Initialize the task worker.

        Args:
            max_workers: Maximum number of concurrent worker tasks
            task_timeout: Default timeout in seconds for tasks (None for no timeout)
            worker_poll_interval: How frequently workers check for new tasks
            cache_enabled: Whether task result caching is enabled
            cache_ttl: Default time-to-live for cached results in seconds
            cache_max_size: Maximum number of entries in the cache
            cache_adapter: Custom cache adapter (default is in-memory)
            max_queue_size: Maximum number of items in the queue
            task_retention_days: Days to keep completed tasks
            cleanup_interval: Seconds between cleanup operations
        """
        # Task storage and synchronization
        self.tasks: Dict[str, TaskInfo] = {}
        self.tasks_lock = asyncio.Lock()

        # Initialize cache
        adapter = cache_adapter or MemoryCacheAdapter(max_size=cache_max_size)
        self.cache = TaskCache(adapter, default_ttl=cache_ttl, enabled=cache_enabled)

        # Create task queue
        self.queue = TaskQueue(max_size=max_queue_size)

        # Create task executor
        self.task_executor = TaskExecutor(cache_manager=self.cache)

        # Create future manager
        self.future_manager = TaskFutureManager()

        # Create worker pool with callbacks for task state management
        self.worker_pool = WorkerPool(
            task_queue=self.queue,
            task_executor=self.task_executor,
            max_workers=max_workers,
            worker_poll_interval=worker_poll_interval,
            on_task_start=self._on_task_started,
            on_task_complete=self._on_task_completed,
            on_task_failed=self._on_task_failed,
            on_task_cancelled=self._on_task_cancelled
        )

        # Configuration
        self.default_task_timeout = task_timeout
        self.task_retention_days = task_retention_days
        self.cleanup_interval = cleanup_interval

        # State
        self.running = False
        self.cleanup_task: Optional[asyncio.Task] = None

        # Use a weak reference dictionary to prevent memory leaks from orphaned futures
        self._task_futures = weakref.WeakValueDictionary()

    async def __aenter__(self) -> Self:
        """Support async context manager protocol."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Support async context manager protocol."""
        await self.stop()

    async def start(self) -> None:
        """Start the worker pool and cleanup task."""
        if self.running:
            return

        self.running = True

        # Start worker pool
        await self.worker_pool.start()

        # Start task cleanup if retention period is set
        if self.task_retention_days is not None:
            self.cleanup_task = asyncio.create_task(self._cleanup_loop())
            self.cleanup_task.set_name("task_cleanup")

        logger.info(f"Started AsyncTaskWorker with {self.worker_pool.worker_count} workers")

    async def stop(self, timeout: float = 5.0) -> None:
        """Stop the worker pool gracefully, waiting for tasks to complete."""
        if not self.running:
            return

        self.running = False
        logger.info("Stopping AsyncTaskWorker...")

        # Cancel cleanup task
        if self.cleanup_task and not self.cleanup_task.done():
            self.cleanup_task.cancel()
            try:
                await asyncio.wait_for(self.cleanup_task, timeout=1.0)
            except asyncio.TimeoutError:
                logger.warning("Cleanup task did not complete in time")
            except asyncio.CancelledError:
                logger.debug("Cleanup task cancelled")
            except Exception as e:
                logger.error(f"Error during cleanup task cancellation: {str(e)}")

        # Cancel all pending futures
        try:
            await self.future_manager.cancel_all_futures()
        except Exception as e:
            logger.error(f"Error cancelling futures: {str(e)}")

        # Stop worker pool
        try:
            await self.worker_pool.stop(timeout=timeout)
        except Exception as e:
            logger.error(f"Error stopping worker pool: {str(e)}")

        self.queue.clear_tracking()
        logger.info("AsyncTaskWorker stopped")

    async def add_task(
            self,
            task_func: Callable[..., Awaitable[T]],
            *args: Any,
            priority: int = 0,
            task_id: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None,
            timeout: Optional[int] = None,
            use_cache: bool = True,
            cache_ttl: Optional[int] = None,
            **kwargs: Any
    ) -> str:
        """
        Add a new task to the queue and return its ID.

        Args:
            task_func: Async function to execute
            *args: Positional arguments to pass to the function
            priority: Task priority (lower number = higher priority)
            task_id: Optional custom task ID (default: auto-generated UUID)
            metadata: Optional metadata to store with the task
            timeout: Optional per-task timeout override
            use_cache: Whether to use cache for this task
            cache_ttl: Optional cache TTL override
            **kwargs: Keyword arguments to pass to the function

        Returns:
            Task ID string
        """
        if not self.running:
            raise RuntimeError("Worker pool is not running")

            # Generate or use provided task ID
        if task_id is None:
            task_id = str(uuid.uuid4())

            # Create task info first under lock
        task_info = TaskInfo(
            id=task_id,
            status=TaskStatus.PENDING,
            created_at=datetime.now(),
            metadata=metadata or {}
        )

        async with self.tasks_lock:
            self.tasks[task_id] = task_info

        # Use task-specific timeout or default
        effective_timeout = timeout if timeout is not None else self.default_task_timeout

        # Add cache options to kwargs
        kwargs["use_cache"] = use_cache
        if cache_ttl is not None:
            kwargs["cache_ttl"] = cache_ttl

        # Add to queue with priority - outside the lock
        await self.queue.put(priority, task_id, task_func, args, kwargs, effective_timeout)
        logger.info(f"Task {task_id} added to queue with priority {priority}")

        return task_id

    async def get_task_info(self, task_id: str) -> Optional[TaskInfo]:
        """
        Get information about a specific task.

        Args:
            task_id: The task ID to look up

        Returns:
            TaskInfo object or None if not found
        """
        async with self.tasks_lock:
            return self.tasks.get(task_id)

    async def get_all_tasks(
            self,
            status: Optional[TaskStatus] = None,
            limit: Optional[int] = None,
            older_than: Optional[timedelta] = None
    ) -> List[TaskInfo]:
        """
        Get information about tasks, with optional filtering.

        Args:
            status: Filter by task status
            limit: Maximum number of tasks to return
            older_than: Only return tasks created before this time delta

        Returns:
            List of TaskInfo objects
        """
        results = []
        cutoff = datetime.now() - older_than if older_than else None

        # Lock to prevent modifications during iteration
        async with self.tasks_lock:
            for task_info in self.tasks.values():
                # Apply filters
                if status is not None and task_info.status != status:
                    continue
                if cutoff is not None and task_info.created_at >= cutoff:
                    continue

                # Add to results
                results.append(task_info)

                # Check limit
                if limit is not None and len(results) >= limit:
                    break

        # Sort by creation time (newest first)
        results.sort(key=lambda t: t.created_at, reverse=True)

        return results

    async def invalidate_cache(
            self,
            task_func: Callable,
            *args: Any,
            **kwargs: Any
    ) -> bool:
        """
        Invalidate cache for a specific task function and arguments.

        Args:
            task_func: The task function
            *args: Function arguments
            **kwargs: Function keyword arguments

        Returns:
            True if cache entry was found and invalidated
        """
        if not self.cache.enabled:
            return False

        return await self.cache.invalidate(task_func.__name__, args, kwargs)

    async def clear_cache(self) -> None:
        """Clear all cached task results."""
        if self.cache.enabled:
            await self.cache.clear()

    async def get_task_future(self, task_id: str) -> asyncio.Future:
        """
        Get a future that will be resolved when the task completes.

        Args:
            task_id: ID of the task to track

        Returns:
            Future that resolves to the task result or raises an exception

        Raises:
            KeyError: If task_id is not found
        """
        async with self.tasks_lock:
            if task_id not in self.tasks:
                raise KeyError(f"Task {task_id} not found")

            # Check if we already have a future for this task
            if task_id in self._task_futures:
                return self._task_futures[task_id]

            # Get the task info
            task_info = self.tasks[task_id]

            # Get or create a future for this task
            future = await self.future_manager.get_future(task_id)

            # Store in our weak dictionary
            self._task_futures[task_id] = future

            # If task is already in terminal state, resolve the future
            if task_info.is_terminal_state():
                await self.future_manager.complete_from_task_info(task_id, task_info)

            return future

    async def wait_for_tasks(self, task_ids: List[str], timeout: Optional[float] = None) -> List[T]:
        """
        Wait for all specified tasks to complete.

        Args:
            task_ids: List of task IDs to wait for
            timeout: Optional timeout in seconds

        Returns:
            List of task results in same order as task_ids

        Raises:
            TimeoutError: If the timeout is reached
        """
        if not task_ids:
            return []

        # Check if any tasks are already complete and get their results directly
        pending_task_ids = []
        final_results = [None] * len(task_ids)

        for i, task_id in enumerate(task_ids):
            async with self.tasks_lock:
                if self.tasks.get(task_id) and self.tasks.get(task_id).status == TaskStatus.COMPLETED:
                    # Task already completed, add result directly
                    final_results[i] = self.tasks.get(task_id).result
                else:
                    # Task still pending, add to list for waiting
                    pending_task_ids.append((i, task_id))

        # If all tasks are already complete, return results immediately
        if not pending_task_ids:
            return final_results

        # Get futures for pending tasks
        futures = []
        for i, task_id in pending_task_ids:
            future = await self.get_task_future(task_id)
            futures.append((i, future))

        # Wait for all pending futures to complete with timeout
        try:
            if timeout is not None:
                async with asyncio.timeout(timeout):
                    for i, future in futures:
                        result = await future
                        final_results[i] = result
            else:
                for i, future in futures:
                    result = await future
                    final_results[i] = result

            return final_results

        except TimeoutError:
            raise TimeoutError("Timeout waiting for tasks to complete")

    async def wait_for_any_task(self, task_ids: List[str], timeout: Optional[float] = None) -> Tuple[T, str]:
        """
        Wait for any of the specified tasks to complete.

        Args:
            task_ids: List of task IDs to wait for
            timeout: Optional timeout in seconds

        Returns:
            Tuple of (result, task_id) for the first completed task

        Raises:
            TimeoutError: If the timeout is reached
            ValueError: If no task IDs are provided
        """
        if not task_ids:
            raise ValueError("No task IDs provided")

        # First check if any tasks are already complete
        for task_id in task_ids:
            async with self.tasks_lock:
                if self.tasks.get(task_id) and self.tasks.get(task_id).status == TaskStatus.COMPLETED:
                    # Task already completed, return its result immediately
                    return self.tasks.get(task_id).result, task_id

        # Get futures for all tasks
        futures_map = {}  # Maps Future -> task_id
        futures = []

        for task_id in task_ids:
            future = await self.get_task_future(task_id)
            futures.append(future)
            futures_map[future] = task_id

        # Wait for first completion
        try:
            if timeout is not None:
                async with asyncio.timeout(timeout):
                    # Create a task for each future and wait for the first to complete
                    done, pending = await asyncio.wait(
                        futures,
                        return_when=asyncio.FIRST_COMPLETED
                    )
            else:
                done, pending = await asyncio.wait(
                    futures,
                    return_when=asyncio.FIRST_COMPLETED
                )

            if not done:
                raise TimeoutError("Timeout waiting for any task to complete")

            # Get the first completed future
            completed_future = next(iter(done))
            task_id = futures_map[completed_future]
            result = completed_future.result()

            return result, task_id

        except TimeoutError:
            raise TimeoutError("Timeout waiting for any task to complete")

    async def cancel_task(self, task_id: str) -> bool:
        """
        Cancel a task.

        Args:
            task_id: ID of the task to cancel

        Returns:
            True if the task was cancelled, False if not found or already completed
        """
        logger.info(f"Attempting to cancel task {task_id}")

        # Acquire the tasks_lock to check and update task state
        async with self.tasks_lock:
            # Check if task exists and can be cancelled
            task_info = self.tasks.get(task_id)
            if not task_info:
                logger.warning(f"Task {task_id} not found for cancellation.")
                return False

            if task_info.is_terminal_state():
                logger.info(f"Task {task_id} is already in a terminal state ({task_info.status}).")
                return False

            logger.info(f"Task {task_id} is in state {task_info.status}")

            # First try to remove from queue if it's there
            # This avoids the race condition by attempting removal directly
            # rather than checking first, then removing later
            task_removed = await self.queue.remove_task(task_id)
            
            if task_removed:
                # Task was in the queue and successfully removed
                logger.info(f"Task {task_id} was queued and has been removed from queue.")
                await task_info.mark_cancelled("Task cancelled before execution")
                logger.info(f"Cancelled queued task {task_id}")

                # Notify futures
                await self.future_manager.set_exception(
                    task_id,
                    TaskError("Task cancelled", task_id=task_id)
                )
                return True

            # For running tasks, we need to identify if it's actually running
            # and prepare it for cancellation
            is_running = task_info.status == TaskStatus.RUNNING

        # 3. Handle running tasks outside the lock to avoid deadlocks
        if is_running:
            # Try to cancel the actual running task in the worker pool
            # This doesn't modify task state, just cancels the asyncio task
            task_cancelled = await self.worker_pool.cancel_running_task(task_id)

            # 4. Re-acquire the lock to update task state
            async with self.tasks_lock:
                task_info = self.tasks.get(task_id)
                # Task might have completed while we were trying to cancel it
                if not task_info or task_info.is_terminal_state():
                    logger.info(f"Task {task_id} already completed or was removed.")
                    return False

                # Mark the task as cancelled
                await task_info.mark_cancelled(
                    "Task execution cancelled" if task_cancelled else "Task cancelled by request"
                )
                logger.info(f"Marked task {task_id} as cancelled")

                # Notify futures
                await self.future_manager.set_exception(
                    task_id,
                    TaskError("Task cancelled", task_id=task_id)
                )
                return True
        else:
            # Task exists but is not running or queued (e.g., PENDING)
            # Re-acquire the lock to update its state
            async with self.tasks_lock:
                task_info = self.tasks.get(task_id)
                # Double-check if it still exists and is not in terminal state
                if not task_info or task_info.is_terminal_state():
                    logger.info(f"Task {task_id} already completed or was removed.")
                    return False

                # Mark it as cancelled
                await task_info.mark_cancelled("Task cancelled before execution")
                logger.info(f"Cancelled pending task {task_id}")

                # Notify futures
                await self.future_manager.set_exception(
                    task_id,
                    TaskError("Task cancelled", task_id=task_id)
                )
                return True

    async def _cleanup_loop(self) -> None:
        """Periodically clean up old completed tasks"""
        logger.debug("Task cleanup loop started")

        while self.running:
            try:
                await asyncio.sleep(self.cleanup_interval)
                await self.cleanup_old_tasks()
            except asyncio.CancelledError:
                logger.debug("Task cleanup loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in task cleanup loop: {str(e)}", exc_info=True)

        logger.debug("Task cleanup loop stopped")

    # Callback methods for WorkerPool
    async def _on_task_started(self, task_id: str, task_info: Optional[TaskInfo], _: Any) -> None:
        """
        Callback when a task is started.
        
        Args:
            task_id: ID of the task
            task_info: Task info from WorkerPool (not used in AsyncTaskWorker implementation, 
                      which retrieves task info from its own tasks dict)
            _: Not used
        """
        async with self.tasks_lock:
            if task_id in self.tasks:
                await self.tasks[task_id].mark_started()

    async def _on_task_completed(self, task_id: str, task_info: Optional[TaskInfo], result: Any) -> None:
        """
        Callback when a task is completed.
        
        Args:
            task_id: ID of the task
            task_info: Task info from WorkerPool (not used in AsyncTaskWorker implementation, 
                      which retrieves task info from its own tasks dict)
            result: Task result
        """
        async with self.tasks_lock:
            if task_id in self.tasks:
                await self.tasks[task_id].mark_completed(result)
                # Complete any futures waiting on this task
                await self.future_manager.complete_from_result(task_id, result)

    # noinspection PyUnusedLocal
    async def _on_task_failed(self, task_id: str, task_info: Optional[TaskInfo], error: str) -> None:
        """
        Callback when a task fails.
        
        Args:
            task_id: ID of the task
            task_info: Task info from WorkerPool (not used in AsyncTaskWorker implementation, 
                      which retrieves task info from its own tasks dict)
            error: Error message
        """
        async with self.tasks_lock:
            if task_id in self.tasks:
                await self.tasks[task_id].mark_failed(error)
                # Set exception on any futures waiting on this task
                await self.future_manager.set_exception(task_id, TaskError(error, task_id=task_id))

    # noinspection PyUnusedLocal
    async def _on_task_cancelled(self, task_id: str, task_info: Optional[TaskInfo], reason: str) -> None:
        """
        Callback when a task is cancelled.
        
        Args:
            task_id: ID of the task
            task_info: Task info from WorkerPool (not used in AsyncTaskWorker implementation, 
                      which retrieves task info from its own tasks dict)
            reason: Reason for cancellation
        """
        async with self.tasks_lock:
            if task_id in self.tasks:
                await self.tasks[task_id].mark_cancelled(reason)
                # Set exception on any futures waiting on this task
                await self.future_manager.set_exception(task_id, TaskError("Task cancelled", task_id=task_id))

    async def cleanup_old_tasks(self) -> int:
        """
        Remove old completed, failed, or cancelled tasks.

        Returns:
            Number of tasks removed
        """
        if self.task_retention_days is None:
            return 0

        cutoff_time = datetime.now() - timedelta(days=self.task_retention_days)
        to_remove = []

        async with self.tasks_lock:
            # First identify tasks to remove
            for task_id, task_info in self.tasks.items():
                # Only remove tasks that are in a final state
                if task_info.is_terminal_state():
                    # Check if the task is old enough to be removed
                    if task_info.completed_at and task_info.completed_at < cutoff_time:
                        to_remove.append(task_id)

            # Then remove them and ensure all references are cleaned up
            count = 0
            for task_id in to_remove:
                if task_id in self.tasks:
                    del self.tasks[task_id]
                    count += 1

        # Clean up any future references
        for task_id in to_remove:
            # Cancel and remove any task futures without waiting
            await self.future_manager.cancel_future(task_id)

        # Also check for any lingering entries in queue tracking
        await self.queue.clean_cancelled_tasks(to_remove)

        logger.info(f"Cleaned up {count} old tasks")

        return count
