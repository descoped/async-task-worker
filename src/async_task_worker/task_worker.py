"""
Async Task Worker

This module provides an asynchronous task worker that manages background tasks
using Python asyncio features with support for task result caching.
"""

import asyncio
import inspect
import logging
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Self, TypeVar

from pydantic import BaseModel, Field

from async_task_worker.task_cache import CacheAdapter, MemoryCacheAdapter, TaskCache

logger = logging.getLogger(__name__)


# Task status enum
class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Task info model
class TaskInfo(BaseModel):
    id: str
    status: TaskStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    progress: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True


# Progress callback type
ProgressCallback = Callable[[float], None]
T = TypeVar('T')


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
            task_timeout: Optional[int] = None,
            worker_poll_interval: float = 1.0,
            cache_enabled: bool = False,
            cache_ttl: Optional[int] = 3600,  # 1 hour default
            cache_max_size: Optional[int] = 1000,
            cache_adapter: Optional[CacheAdapter] = None
    ):
        """
        Initialize the task worker.

        Args:
            max_workers: Maximum number of concurrent worker tasks
            task_timeout: Default timeout in seconds for tasks (None for no timeout)
            worker_poll_interval: How frequently workers check for new tasks
            cache_enabled: Whether task result caching is enabled
            cache_ttl: Default time-to-live for cached results in seconds (None for no expiry)
            cache_max_size: Maximum number of entries in the cache (None for unlimited)
            cache_adapter: Custom cache adapter (default is in-memory)
        """
        self.tasks: Dict[str, TaskInfo] = {}
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.max_workers = max_workers
        self.default_task_timeout = task_timeout
        self.worker_poll_interval = worker_poll_interval
        self.workers: List[asyncio.Task] = []
        self.running = False
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._progress_callbacks: Dict[str, ProgressCallback] = {}
        self._completion_events: Dict[str, asyncio.Event] = {}
        self._locks = {
            "tasks": asyncio.Lock(),
            "running_tasks": asyncio.Lock(),
            "progress_callbacks": asyncio.Lock(),
            "completion_events": asyncio.Lock()
        }

        # Set up cache
        adapter = cache_adapter or MemoryCacheAdapter(max_size=cache_max_size)
        self.cache = TaskCache(adapter, default_ttl=cache_ttl, enabled=cache_enabled)

    async def __aenter__(self) -> Self:
        """Support async context manager protocol"""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Support async context manager protocol"""
        await self.stop()

    async def start(self) -> None:
        """Start the worker pool"""
        if self.running:
            return

        self.running = True
        # Start worker tasks
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker_loop(worker_id=i))
            worker.set_name(f"task_worker_{i}")
            self.workers.append(worker)

        logger.info(f"Started {self.max_workers} worker tasks")

    async def stop(self, timeout: float = 5.0) -> None:
        """
        Stop the worker pool gracefully.

        Args:
            timeout: Maximum time to wait for tasks to complete
        """
        if not self.running:
            return

        self.running = False
        logger.info("Stopping worker pool...")

        # Cancel all running tasks
        async with self._locks["running_tasks"]:
            for task_id, task in self._running_tasks.items():
                if not task.done():
                    logger.info(f"Cancelling task {task_id}")
                    task.cancel()

                    # Update status immediately to fix test_task_cancellation
                    async with self._locks["tasks"]:
                        if task_id in self.tasks:
                            self.tasks[task_id].status = TaskStatus.CANCELLED
                            self.tasks[task_id].completed_at = datetime.now()
                            self.tasks[task_id].error = "Task cancelled"

        # Give workers time to process cancellations
        if self.workers:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self.workers, return_exceptions=True),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                logger.warning(f"Timeout waiting for workers to stop after {timeout}s")

        self.workers = []

        async with self._locks["running_tasks"]:
            self._running_tasks = {}

        logger.info("Worker pool stopped")

    async def _update_progress(self, task_id: str, progress: float) -> None:
        """Update task progress with proper locking"""
        async with self._locks["tasks"]:
            if task_id in self.tasks:
                self.tasks[task_id].progress = progress

    async def _worker_loop(self, worker_id: int) -> None:
        """Main worker loop that processes tasks from the queue."""
        logger.debug(f"Worker {worker_id} started")

        while self.running:
            try:
                # Use a try/except block just for the queue.get operation
                try:
                    priority, task_id, task_func, task_args, task_kwargs, timeout = await asyncio.wait_for(
                        self.queue.get(), timeout=self.worker_poll_interval
                    )

                    logger.info(f"Worker {worker_id} processing task {task_id} (priority: {priority})")

                    # Update task status
                    async with self._locks["tasks"]:
                        if task_id not in self.tasks:
                            logger.warning(f"Task {task_id} not found in tasks dictionary")
                            self.queue.task_done()
                            continue

                        self.tasks[task_id].status = TaskStatus.RUNNING
                        self.tasks[task_id].started_at = datetime.now()

                    # Use the per-task timeout if provided
                    await self._execute_task(task_id, task_func, task_args, task_kwargs, timeout)

                    # Mark task as done in queue
                    self.queue.task_done()

                except asyncio.TimeoutError:
                    # No task received, continue loop
                    continue

            except asyncio.CancelledError:
                # Worker is being cancelled
                logger.debug(f"Worker {worker_id} cancelled")
                break

            except Exception as e:
                # Log unexpected exceptions but continue processing
                logger.error(f"Unexpected error in worker {worker_id}: {str(e)}", exc_info=True)

        logger.debug(f"Worker {worker_id} stopped")

    async def _execute_task(
            self,
            task_id: str,
            task_func: Callable,
            task_args: tuple,
            task_kwargs: dict,
            timeout: Optional[int] = None
    ) -> Any:
        """Execute a task with the specified timeout."""

        # Extract cache options from kwargs if present
        use_cache = task_kwargs.pop("use_cache", True)
        cache_ttl = task_kwargs.pop("cache_ttl", None)

        # Try to get from cache if caching is enabled
        if use_cache and self.cache.enabled:
            func_name = task_func.__name__
            # Make a copy without progress_callback for cache key
            cache_kwargs = {k: v for k, v in task_kwargs.items()
                            if k != "progress_callback"}

            cache_hit, cached_result = await self.cache.get(func_name, task_args, cache_kwargs)

            if cache_hit:
                logger.info(f"Task {task_id} using cached result")

                # Update task with cached result
                async with self._locks["tasks"]:
                    if task_id in self.tasks:  # Guard against race conditions
                        self.tasks[task_id].status = TaskStatus.COMPLETED
                        self.tasks[task_id].completed_at = datetime.now()
                        self.tasks[task_id].result = cached_result
                        self.tasks[task_id].progress = 1.0

                return cached_result

        # Create progress callback
        def update_progress(progress: float) -> None:
            asyncio.create_task(self._update_progress(task_id, progress))

        # Create a copy of kwargs to avoid modifying the original
        task_kwargs_copy = task_kwargs.copy()

        # Check if the function can accept progress_callback
        try:
            # Get the signature and check if it has **kwargs or explicit progress_callback
            sig = inspect.signature(task_func)
            has_progress_param = "progress_callback" in sig.parameters
            has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

            # Only add if the function can accept it
            if has_progress_param or has_kwargs:
                task_kwargs_copy["progress_callback"] = update_progress
        except (ValueError, TypeError):
            # If we can't inspect the function, don't add the callback
            pass

        # Create the task coroutine
        task_coroutine = task_func(*task_args, **task_kwargs_copy)

        # Apply timeout if configured
        if timeout is not None:
            task_coroutine = asyncio.wait_for(task_coroutine, timeout=timeout)

        # Execute the task
        execution_task = asyncio.create_task(task_coroutine)

        async with self._locks["running_tasks"]:
            self._running_tasks[task_id] = execution_task

        try:
            # Wait for task to complete
            result = await execution_task

            # Update task with success
            async with self._locks["tasks"]:
                if task_id in self.tasks:  # Guard against race conditions
                    self.tasks[task_id].status = TaskStatus.COMPLETED
                    self.tasks[task_id].completed_at = datetime.now()
                    self.tasks[task_id].result = result
                    self.tasks[task_id].progress = 1.0

            logger.info(f"Task {task_id} completed successfully")

            # Store in cache if caching is enabled
            if use_cache and self.cache.enabled:
                # Get function name and kwargs without progress_callback
                func_name = task_func.__name__
                cache_kwargs = {k: v for k, v in task_kwargs.items()
                                if k != "progress_callback"}

                # Store in cache
                await self.cache.set(func_name, task_args, cache_kwargs, result, cache_ttl)

            return result

        except asyncio.CancelledError:
            # Task was cancelled
            async with self._locks["tasks"]:
                if task_id in self.tasks:
                    self.tasks[task_id].status = TaskStatus.CANCELLED
                    self.tasks[task_id].completed_at = datetime.now()
                    self.tasks[task_id].error = "Task cancelled"

            logger.info(f"Task {task_id} was cancelled")
            raise

        except asyncio.TimeoutError:
            # Task timed out
            async with self._locks["tasks"]:
                if task_id in self.tasks:
                    self.tasks[task_id].status = TaskStatus.FAILED
                    self.tasks[task_id].completed_at = datetime.now()
                    self.tasks[task_id].error = f"Task timed out after {timeout}s"

            logger.error(f"Task {task_id} timed out")
            raise

        except Exception as e:
            # Task failed with an exception
            async with self._locks["tasks"]:
                if task_id in self.tasks:
                    self.tasks[task_id].status = TaskStatus.FAILED
                    self.tasks[task_id].completed_at = datetime.now()
                    self.tasks[task_id].error = str(e)

            logger.error(f"Task {task_id} failed: {str(e)}", exc_info=True)
            raise

        finally:
            # Clean up
            async with self._locks["running_tasks"]:
                if task_id in self._running_tasks:
                    del self._running_tasks[task_id]

            async with self._locks["progress_callbacks"]:
                if task_id in self._progress_callbacks:
                    del self._progress_callbacks[task_id]

            # Signal completion for any waiting futures
            async with self._locks["completion_events"]:
                if task_id in self._completion_events:
                    self._completion_events[task_id].set()

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
            timeout: Optional per-task timeout override (uses default if None)
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

        # Create task info
        async with self._locks["tasks"]:
            self.tasks[task_id] = TaskInfo(
                id=task_id,
                status=TaskStatus.PENDING,
                created_at=datetime.now(),
                metadata=metadata or {}
            )

        # Use task-specific timeout or default
        effective_timeout = timeout if timeout is not None else self.default_task_timeout

        # Add cache options to kwargs
        kwargs["use_cache"] = use_cache
        if cache_ttl is not None:
            kwargs["cache_ttl"] = cache_ttl

        # Add to queue with priority
        await self.queue.put((priority, task_id, task_func, args, kwargs, effective_timeout))
        logger.info(f"Task {task_id} added to queue with priority {priority}")

        return task_id

    def get_task_info(self, task_id: str) -> Optional[TaskInfo]:
        """
        Get information about a specific task.

        Args:
            task_id: The task ID to look up

        Returns:
            TaskInfo object or None if not found
        """
        # No need for lock here as we're just reading
        return self.tasks.get(task_id)

    def get_all_tasks(
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
        # Make a copy of tasks to avoid mutation during iteration
        tasks = list(self.tasks.values())

        # Apply status filter
        if status is not None:
            tasks = [t for t in tasks if t.status == status]

        # Apply age filter
        if older_than is not None:
            cutoff = datetime.now() - older_than
            tasks = [t for t in tasks if t.created_at < cutoff]

        # Sort by creation time (newest first)
        tasks.sort(key=lambda t: t.created_at, reverse=True)

        # Apply limit
        if limit is not None:
            tasks = tasks[:limit]

        return tasks

    async def cancel_task(self, task_id: str) -> bool:
        """
        Cancel a running or pending task.

        Args:
            task_id: ID of the task to cancel

        Returns:
            True if the task was cancelled, False if not found or already completed
        """
        # Check if task exists and get task info
        async with self._locks["tasks"]:
            if task_id not in self.tasks:
                return False

            task_info = self.tasks[task_id]

            # Can't cancel completed tasks
            if task_info.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                return False

        # If task is running, cancel it
        async with self._locks["running_tasks"]:
            running_task = self._running_tasks.get(task_id)

        if running_task and not running_task.done():
            running_task.cancel()
            # Update task status immediately
            async with self._locks["tasks"]:
                task_info.status = TaskStatus.CANCELLED
                task_info.completed_at = datetime.now()
                task_info.error = "Task cancelled"
            logger.info(f"Cancelled running task {task_id}")
            return True

        # Task is pending, mark as cancelled
        async with self._locks["tasks"]:
            task_info.status = TaskStatus.CANCELLED
            task_info.completed_at = datetime.now()
            task_info.error = "Task cancelled before execution"
        logger.info(f"Cancelled pending task {task_id}")
        return True

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

    def get_task_future(self, task_id: str) -> asyncio.Future:
        """
        Get a future that will be resolved when the task completes.

        Args:
            task_id: ID of the task to track

        Returns:
            Future that resolves to the task result or raises an exception

        Raises:
            KeyError: If task_id is not found
        """
        if task_id not in self.tasks:
            raise KeyError(f"Task {task_id} not found")

        # Create future for the result
        future = asyncio.Future()
        task_info = self.tasks[task_id]

        # If already complete, return pre-resolved future
        if task_info.status == TaskStatus.COMPLETED:
            future.set_result(task_info.result)
        elif task_info.status == TaskStatus.FAILED:
            future.set_exception(RuntimeError(f"Task failed: {task_info.error}"))
        elif task_info.status == TaskStatus.CANCELLED:
            future.set_exception(asyncio.CancelledError(f"Task was cancelled: {task_info.error}"))
        else:
            # Create event and register monitoring task
            event = asyncio.Event()
            asyncio.create_task(self._wait_for_completion(task_id, future, event))
            asyncio.create_task(self._register_completion_event(task_id, event))

        return future

    def get_task_futures(self, task_ids: List[str]) -> List[asyncio.Future]:
        """
        Get futures for multiple tasks at once.

        Args:
            task_ids: List of task IDs to monitor

        Returns:
            List of futures that will resolve to task results

        Raises:
            KeyError: If any task_id is not found
        """
        return [self.get_task_future(task_id) for task_id in task_ids]

    async def _register_completion_event(self, task_id: str, event: asyncio.Event) -> None:
        """Register a completion event for a task"""
        async with self._locks["completion_events"]:
            self._completion_events[task_id] = event

    async def _wait_for_completion(self, task_id: str, future: asyncio.Future, event: asyncio.Event) -> None:
        """Wait for task completion using an event"""
        try:
            # Wait for the event to be set
            await event.wait()

            # Get final task info
            task_info = self.get_task_info(task_id)

            # Handle the result based on status
            if task_info is None:
                future.set_exception(RuntimeError(f"Task {task_id} was removed"))
            elif task_info.status == TaskStatus.COMPLETED:
                future.set_result(task_info.result)
            elif task_info.status == TaskStatus.FAILED:
                future.set_exception(RuntimeError(f"Task failed: {task_info.error}"))
            elif task_info.status == TaskStatus.CANCELLED:
                future.set_exception(asyncio.CancelledError(f"Task was cancelled: {task_info.error}"))
            else:
                future.set_exception(RuntimeError(f"Task in unexpected state: {task_info.status}"))
        except Exception as e:
            if not future.done():
                future.set_exception(e)
        finally:
            # Clean up the event
            async with self._locks["completion_events"]:
                if task_id in self._completion_events:
                    del self._completion_events[task_id]
