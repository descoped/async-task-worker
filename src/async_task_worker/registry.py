"""
Task Registry System

This module provides a task registration system with manual registration.
"""
import inspect
import logging
import threading
from functools import wraps
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

logger = logging.getLogger(__name__)

# Type definitions
T = TypeVar('T')  # Used for generic task function return type
TaskFunc = Callable[..., Awaitable[T]]

# Global task registry
_TASK_REGISTRY: Dict[str, TaskFunc] = {}

# Use a standard threading lock for synchronous operations
_REGISTRY_LOCK = threading.RLock()


def task(task_type: str) -> Callable[[TaskFunc], TaskFunc]:
    """
    Decorator to register a function as a task handler.

    Example:
        @task("process_data")
        async def process_data_task(data: dict, config: dict) -> dict:
            # Process data
            return result
    """

    def decorator(func: TaskFunc) -> TaskFunc:
        # Call synchronous register_task
        # We can't await an async function in a decorator
        register_task(task_type, func)

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await func(*args, **kwargs)

        return wrapper

    return decorator


def register_task(task_type: str, task_func: TaskFunc) -> None:
    """
    Register a task function for a specific task type (synchronous version).

    Args:
        task_type: Unique identifier for the task type
        task_func: Async function that implements the task
    """
    if not task_type or not isinstance(task_type, str):
        raise ValueError("Task type must be a non-empty string")

    # Ensure task_func is an async function
    if not inspect.iscoroutinefunction(task_func):
        raise TypeError(f"Task function for {task_type} must be an async function")

    # Use the threading lock for thread-safe registry updates
    with _REGISTRY_LOCK:
        if task_type in _TASK_REGISTRY:
            logger.warning(f"Overriding existing task handler for {task_type}")
        _TASK_REGISTRY[task_type] = task_func

    logger.info(f"Registered task handler for {task_type}")


async def get_task_function(task_type: str) -> Optional[TaskFunc]:
    """
    Get the task function for a specific task type.

    Args:
        task_type: The task type to look up

    Returns:
        The task function or None if not found
    """
    # No need for locks for read operations on simple dictionary lookup
    return _TASK_REGISTRY.get(task_type)


async def get_all_task_types() -> List[str]:
    """
    Get a list of all registered task types.

    Returns:
        List of registered task type names
    """
    # No need for locks for read operations on a simple list copy
    return list(_TASK_REGISTRY.keys())
