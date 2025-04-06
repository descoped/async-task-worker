"""
Async Task Worker
A robust asynchronous task worker system for Python applications.
"""
# Export cache-related classes
from task_worker.task_cache import (
    CacheAdapter,
    MemoryCacheAdapter,
    TaskCache,
)
from task_worker.task_registry import (
    task,
    register_task,
    get_task_function,
    get_all_task_types,
)
# Export main classes and functions
from task_worker.task_worker import (
    AsyncTaskWorker,
    TaskInfo,
    TaskStatus,
    ProgressCallback,
)
# Export API router factory
from task_worker.task_worker_api import create_task_worker_router

# Define what gets imported with `from task_worker import *`
__all__ = [
    'AsyncTaskWorker',
    'TaskInfo',
    'TaskStatus',
    'ProgressCallback',
    'task',
    'register_task',
    'get_task_function',
    'get_all_task_types',
    'CacheAdapter',
    'MemoryCacheAdapter',
    'TaskCache',
    'create_task_worker_router',
]
