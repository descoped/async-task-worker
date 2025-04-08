"""
Task Executor Module with improved type annotations

This module handles the execution of tasks, including timeouts,
error handling, and progress reporting.
"""

import asyncio
import inspect
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple, TypeVar, Protocol, runtime_checkable

from async_task_worker.exceptions import TaskCancellationError, TaskExecutionError, TaskTimeoutError
from async_task_worker.status import TaskInfo

logger = logging.getLogger(__name__)

# Type definitions
T = TypeVar('T')  # Used for generic task function return type


@runtime_checkable
class ProgressCallback(Protocol):
    """Type protocol for progress callback functions"""

    def __call__(self, progress: float) -> None: ...


class CacheManager(Protocol):
    """Type protocol for cache manager"""
    enabled: bool

    async def get(
            self,
            func_name: str,
            args: Tuple,
            kwargs: Dict,
            *,
            cache_key_fn: Optional[Callable] = None,
            task_id: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Any]: ...

    async def set(
            self,
            func_name: str,
            args: Tuple,
            kwargs: Dict,
            result: Any,
            *,
            ttl: Optional[int] = None,
            cache_key_fn: Optional[Callable] = None,
            task_id: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None
    ) -> bool: ...


class TaskExecutor:
    """
    Handles the execution of tasks with proper error handling,
    timeout management, and progress reporting.
    """

    def __init__(self, cache_manager: Optional[CacheManager] = None):
        """
        Initialize the task executor.

        Args:
            cache_manager: Optional cache manager for task results
        """
        self.cache_manager = cache_manager

    async def execute_task(
            self,
            task_id: str,
            task_info: Optional[TaskInfo],
            task_func: Callable[..., Awaitable[T]],
            args: Tuple,
            kwargs: Dict[str, Any],
            timeout: Optional[float] = None
    ) -> T:
        """
        Execute a task with timeout and error handling.

        Args:
            task_id: Unique task identifier
            task_info: Task information object
            task_func: Function to execute
            args: Positional arguments
            kwargs: Keyword arguments
            timeout: Optional timeout in seconds

        Returns:
            Task execution result

        Raises:
            TaskExecutionError: If task execution fails
            TaskTimeoutError: If task times out
            TaskCancellationError: If task is cancelled
        """
        start_time = time.time()

        # Ensure task_func is a coroutine function
        if not inspect.iscoroutinefunction(task_func):
            error_msg = f"Task {task_id}: Function {task_func.__name__} is not a coroutine function"
            logger.error(error_msg)
            raise TypeError(error_msg)

        # Extract cache options from kwargs if present
        use_cache: bool = kwargs.pop("use_cache", True)
        cache_ttl: Optional[int] = kwargs.pop("cache_ttl", None)

        # Remove special cache control parameters that should not be passed to task function
        if "_cache_entry_id" in kwargs:
            kwargs.pop("_cache_entry_id")

        # Try to get from cache if caching is enabled
        if use_cache and self.cache_manager and self.cache_manager.enabled:
            try:
                func_name = task_func.__name__
                # Extract custom cache entry ID and prepare cache kwargs
                # Make a copy without progress_callback for cache key
                function_kwargs = kwargs.copy()
                cache_entry_id = function_kwargs.pop("_cache_entry_id", task_id)
                cache_kwargs = {k: v for k, v in function_kwargs.items()
                                if k != "progress_callback"}

                cache_hit, cached_result = await self.cache_manager.get(
                    func_name,
                    args,
                    cache_kwargs,
                    task_id=cache_entry_id
                )

                if cache_hit:
                    logger.info(f"Task {task_id} using cached result")
                    return cached_result
            except Exception as e:
                error_msg = f"Cache retrieval error for task {task_id}: {str(e)}"
                logger.error(error_msg,
                             extra={
                                 "task_id": task_id,
                                 "function": task_func.__name__,
                                 "error_type": type(e).__name__,
                                 "cache_operation": "get"
                             },
                             exc_info=True)

                # Don't use cache for this task anymore since retrieval failed
                use_cache = False

                # Continue execution without caching as a fallback strategy
                # raise TaskExecutionError(f"Cache retrieval failed: {error_msg}", original_error=e, task_id=task_id)

        # Create a progress callback
        def progress_callback(progress: float) -> None:
            # Validate progress
            if progress < 0.0:
                progress = 0.0
            elif progress > 1.0:
                progress = 1.0
            # If task_info is provided, update it directly
            if task_info is not None:
                task_info.update_progress(progress)

        # Add progress callback to kwargs if the function accepts it
        task_kwargs_copy = kwargs.copy()
        try:
            # Get the signature and check if it has **kwargs or explicit progress_callback
            sig = inspect.signature(task_func)
            has_progress_param = "progress_callback" in sig.parameters
            has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

            # Only add if the function can accept it
            if has_progress_param or has_kwargs:
                task_kwargs_copy["progress_callback"] = progress_callback
        except (ValueError, TypeError) as e:
            logger.warning(f"Could not inspect function signature for {task_func.__name__}: {str(e)}")
            # If we can't inspect the function, don't add the callback

        # Create task coroutine
        task_coroutine = task_func(*args, **task_kwargs_copy)

        try:
            # Execute the task with timeout if specified
            if timeout is not None:
                async with asyncio.timeout(timeout):
                    result = await task_coroutine
            else:
                result = await task_coroutine

            # Store in cache if enabled
            if use_cache and self.cache_manager and self.cache_manager.enabled:
                try:
                    func_name = task_func.__name__
                    
                    # Extract custom cache entry ID and prepare cache kwargs
                    function_kwargs = kwargs.copy()
                    cache_entry_id = function_kwargs.pop("_cache_entry_id", task_id)
                    cache_kwargs = {k: v for k, v in function_kwargs.items()
                                    if k != "progress_callback"}

                    await self.cache_manager.set(
                        func_name,
                        args,
                        cache_kwargs,
                        result,
                        ttl=cache_ttl,
                        task_id=cache_entry_id
                    )
                except Exception as e:
                    error_msg = f"Error caching result for task {task_id}: {str(e)}"
                    logger.error(error_msg,
                                 extra={
                                     "task_id": task_id,
                                     "function": task_func.__name__,
                                     "error_type": type(e).__name__,
                                     "cache_operation": "set",
                                     "ttl": cache_ttl
                                 },
                                 exc_info=True)
                    # Continue and return the result even if caching fails

            execution_time = time.time() - start_time
            logger.info(f"Task {task_id} completed successfully in {execution_time:.3f}s")

            return result

        except TimeoutError:
            logger.error(f"Task {task_id} timed out after {timeout}s")
            raise TaskTimeoutError(timeout, task_id=task_id)

        except asyncio.CancelledError:
            logger.info(f"Task {task_id} was cancelled")
            raise TaskCancellationError("Task cancelled", task_id=task_id)

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Task {task_id} failed: {error_msg}", exc_info=True)
            raise TaskExecutionError(f"Task execution failed: {error_msg}", original_error=e, task_id=task_id)
