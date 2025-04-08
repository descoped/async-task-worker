"""
Worker Pool Module

This module manages the pool of worker tasks that process
items from the task queue.
"""

import asyncio
import logging
import time
from typing import Callable, Dict, List, Optional, Self, Any, Awaitable, TypeVar

from async_task_worker.executor import TaskExecutor
from async_task_worker.queue import TaskQueue
from async_task_worker.status import TaskInfo

logger = logging.getLogger(__name__)

# Type definitions
T = TypeVar('T')  # Used for generic task function return type

# Define a type for task status update callback
TaskStatusCallback = Callable[[str, Optional[TaskInfo], Any], Awaitable[None]]


class WorkerPool:
    """
    Manages a pool of worker tasks that process items from a task queue.

    Focuses solely on worker lifecycle management and task execution,
    delegating state management to callbacks.
    """

    def __init__(
            self,
            task_queue: TaskQueue,
            task_executor: TaskExecutor,
            max_workers: int = 10,
            worker_poll_interval: float = 1.0,
            on_task_start: Optional[TaskStatusCallback] = None,
            on_task_complete: Optional[TaskStatusCallback] = None,
            on_task_failed: Optional[TaskStatusCallback] = None,
            on_task_cancelled: Optional[TaskStatusCallback] = None,
            log_idle_workers: bool = False,
    ):
        """
        Initialize the worker pool.

        Args:
            task_queue: Queue to get tasks from
            task_executor: Executor for running tasks
            max_workers: Maximum number of concurrent workers
            worker_poll_interval: How often workers check for new tasks
            on_task_start: Callback when a task starts execution
            on_task_complete: Callback when a task completes successfully
            on_task_failed: Callback when a task fails
            on_task_cancelled: Callback when a task is cancelled
            log_idle_workers: Whether to log periodic idle worker messages (default: False)
        """
        self.task_queue = task_queue
        self.task_executor = task_executor
        self.max_workers = max_workers
        self.worker_poll_interval = worker_poll_interval

        # Callbacks for task state changes
        self.on_task_start = on_task_start
        self.on_task_complete = on_task_complete
        self.on_task_failed = on_task_failed
        self.on_task_cancelled = on_task_cancelled
        self.log_idle_workers = log_idle_workers

        self.workers: List[asyncio.Task] = []
        self.running = False
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._running_tasks_lock = asyncio.Lock()
        self._task_to_worker: Dict[str, asyncio.Task] = {}

    async def __aenter__(self) -> Self:
        """Support async context manager protocol."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Support async context manager protocol."""
        await self.stop()

    async def start(self) -> None:
        """Start the worker pool."""
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

        # First cancel all running tasks
        cancelled_tasks = []
        async with self._running_tasks_lock:
            for task_id, task in self._running_tasks.items():
                if not task.done():
                    logger.info(f"Cancelling task {task_id}")
                    task.cancel()
                    cancelled_tasks.append(task_id)

        # Notify about cancelled tasks via callback
        if self.on_task_cancelled and cancelled_tasks:
            for task_id in cancelled_tasks:
                try:
                    await self.on_task_cancelled(task_id, None, "Worker pool shutting down")
                except Exception as e:
                    logger.error(f"Error in task cancelled callback for {task_id}: {str(e)}")

        # Then wait for worker tasks to complete
        if self.workers:
            # First try cooperative cancellation
            for worker in self.workers:
                if not worker.done():
                    worker.cancel()

            # Wait for workers to finish with proper exception handling
            try:
                # Use asyncio.timeout() context manager (Python 3.11+)
                async with asyncio.timeout(timeout):
                    pending = list(self.workers)
                    # Shield to prevent this wait from being cancelled
                    await asyncio.gather(*[asyncio.shield(worker) for worker in pending],
                                         return_exceptions=True)
            except TimeoutError:
                logger.warning(f"Worker pool stop timed out after {timeout}s")
                # Force cancellation for any remaining workers
                for worker in self.workers:
                    if not worker.done():
                        worker.cancel()

                # Give them a moment to finish cancellation
                try:
                    async with asyncio.timeout(0.5):
                        await asyncio.gather(*[w for w in self.workers if not w.done()],
                                             return_exceptions=True)
                except (TimeoutError, asyncio.CancelledError):
                    pass  # Ignore any exceptions here
            except Exception as e:
                logger.error(f"Error during worker shutdown: {str(e)}")
                # Continue with cleanup even if there was an error

        self.workers = []

        # Clean up running tasks dictionary
        async with self._running_tasks_lock:
            self._running_tasks.clear()
            self._task_to_worker.clear()

        logger.info("Worker pool stopped")

    async def _worker_loop(self, worker_id: int) -> None:
        """
        Main worker loop that processes tasks from the queue.

        Args:
            worker_id: Unique identifier for this worker
        """
        logger.debug(f"Worker {worker_id} started")

        last_activity = time.time()
        idle_log_interval = 60  # Log idle status once per minute

        while self.running:
            try:
                # Simplified task processing flow
                task_item = await self._get_next_task(worker_id, last_activity, idle_log_interval, self.log_idle_workers)
                if task_item is None:
                    continue

                last_activity = time.time()
                priority, task_id, task_func, task_args, task_kwargs, timeout = task_item

                # Process the task
                try:
                    await self._process_task(worker_id, task_id, task_func, task_args, task_kwargs, timeout)
                finally:
                    # Always mark task as done in queue
                    self.task_queue.task_done()

            except asyncio.CancelledError:
                logger.debug(f"Worker {worker_id} cancelled")
                break
            except Exception as e:
                logger.error(f"Unexpected error in worker {worker_id}: {str(e)}", exc_info=True)
                await asyncio.sleep(0.1)  # Avoid tight loop on persistent errors

        logger.debug(f"Worker {worker_id} stopped")

    async def _get_next_task(self, worker_id, last_activity, idle_log_interval, log_idle_workers=False):
        """Get the next task with timeout and idle logging"""
        try:
            async with asyncio.timeout(self.worker_poll_interval):
                return await self.task_queue.get()
        except TimeoutError:
            # Log idle status if enabled and needed
            current_time = time.time()
            if log_idle_workers and current_time - last_activity > idle_log_interval:
                logger.debug(f"Worker {worker_id} idle for {current_time - last_activity:.1f}s")
                return None
            # Check if we should continue running
            if not self.running:
                logger.debug(f"Worker {worker_id} cancelled due to shutdown")
                raise asyncio.CancelledError()
            return None

    async def _process_task(self, worker_id, task_id, task_func, task_args, task_kwargs, timeout):
        """Process a single task"""
        logger.info(f"Worker {worker_id} processing task {task_id}")

        # Get task info from the executor's context if needed
        task_info = None  # We don't directly manage TaskInfo anymore

        # Notify task started
        if self.on_task_start:
            try:
                await self.on_task_start(task_id, task_info, None)
            except Exception as e:
                logger.error(f"Error in task start callback for {task_id}: {str(e)}")

        # Execute the task
        execution_task = asyncio.create_task(
            self.task_executor.execute_task(
                task_id, None, task_func, task_args, task_kwargs, timeout
            )
        )
        execution_task.set_name(f"exec_{task_id}")

        # Register running task
        async with self._running_tasks_lock:
            self._running_tasks[task_id] = execution_task
            self._task_to_worker[task_id] = execution_task

        try:
            result = await execution_task

            # Notify task completed
            if self.on_task_complete:
                try:
                    await self.on_task_complete(task_id, task_info, result)
                except Exception as e:
                    logger.error(f"Error in task complete callback for {task_id}: {str(e)}")

        except asyncio.CancelledError:
            logger.info(f"Task {task_id} execution cancelled")

            # Notify task cancelled
            if self.on_task_cancelled:
                try:
                    await self.on_task_cancelled(task_id, task_info, "Task cancelled during execution")
                except Exception as e:
                    logger.error(f"Error in task cancelled callback for {task_id}: {str(e)}")
            raise

        except Exception as e:
            logger.error(f"Error in task {task_id}: {str(e)}", exc_info=True)

            # Notify task failed
            if self.on_task_failed:
                try:
                    await self.on_task_failed(task_id, task_info, str(e))
                except Exception as callback_error:
                    logger.error(f"Error in task failed callback for {task_id}: {str(callback_error)}")
        finally:
            logger.info(f"Task {task_id} finished")
            # Clean up task reference
            async with self._running_tasks_lock:
                if task_id in self._running_tasks:
                    del self._running_tasks[task_id]
                if task_id in self._task_to_worker:
                    del self._task_to_worker[task_id]

    async def cancel_running_task(self, task_id: str) -> bool:
        """
        Cancel a task that is currently running in a worker.
        
        This method focuses only on finding and canceling the actual running task
        in asyncio. Task state updates are handled through callbacks.
        
        The cancellation process is atomic and thread-safe, ensuring that
        the task state is checked and modified under a lock to prevent race conditions.

        Args:
            task_id: ID of the task to cancel

        Returns:
            True if task was found and cancelled, False otherwise
        """
        logger.info(f"Worker pool attempting to cancel running task {task_id}")

        async with self._running_tasks_lock:
            # Check if task exists in our tracking dictionaries
            worker_task = self._task_to_worker.get(task_id)
            if not worker_task:
                logger.warning(f"Worker task for {task_id} not found in running tasks")
                return False
                
            # Check if task is already done before attempting to cancel
            if worker_task.done():
                logger.info(f"Task {task_id} is already completed, cannot cancel")
                # Remove from tracking
                if task_id in self._running_tasks:
                    del self._running_tasks[task_id]
                if task_id in self._task_to_worker:
                    del self._task_to_worker[task_id]
                return False

            # Task exists and is running - cancel it
            logger.info(f"Cancelling running task {task_id}")
            worker_task.cancel()
            
            # Task references are cleaned up in the worker task's finally block
            
            return True

    @property
    def active_tasks_count(self) -> int:
        """Get the number of currently running tasks."""
        return len(self._running_tasks)

    @property
    def worker_count(self) -> int:
        """Get the current number of workers."""
        return len(self.workers)
