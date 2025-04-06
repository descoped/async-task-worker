"""
Task Registry System

This module provides a task registration system with manual registration.
"""

import inspect
import logging
from functools import wraps
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

logger = logging.getLogger(__name__)

# Type definitions
TaskFunc = Callable[..., Awaitable[Any]]

# Global task registry
_TASK_REGISTRY: Dict[str, TaskFunc] = {}


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
        register_task(task_type, func)

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await func(*args, **kwargs)

        return wrapper

    return decorator


def register_task(task_type: str, task_func: TaskFunc) -> None:
    """
    Register a task function for a specific task type.

    Args:
        task_type: Unique identifier for the task type
        task_func: Async function that implements the task
    """
    if task_type in _TASK_REGISTRY:
        logger.warning(f"Overriding existing task handler for {task_type}")

    # Ensure task_func is an async function
    if not inspect.iscoroutinefunction(task_func):
        raise TypeError(f"Task function for {task_type} must be an async function")

    _TASK_REGISTRY[task_type] = task_func
    logger.info(f"Registered task handler for {task_type}")


def get_task_function(task_type: str) -> Optional[TaskFunc]:
    """
    Get the task function for a specific task type.

    Args:
        task_type: The task type to look up

    Returns:
        The task function or None if not found
    """
    return _TASK_REGISTRY.get(task_type)


def get_all_task_types() -> List[str]:
    """
    Get a list of all registered task types.

    Returns:
        List of registered task type names
    """
    return list(_TASK_REGISTRY.keys())
