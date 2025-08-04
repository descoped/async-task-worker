"""
Async Task Worker
A robust asynchronous task worker system for Python applications.
"""

# Export cache adapter
from async_task_worker.adapters.cache_adapter import AsyncCacheAdapter

# Export main AsyncTaskWorker class
from async_task_worker.core import (
    AsyncTaskWorker,
)

# Export error handling classes
from async_task_worker.exceptions import (
    ErrorCategory,
    ErrorHandler,
    TaskCancellationError,
    TaskDefinitionError,
    TaskError,
    TaskExecutionError,
    TaskTimeoutError,
)

# Export task executor
from async_task_worker.executor import (
    ProgressCallback,
    TaskExecutor,
)

# Export task future functionality
from async_task_worker.futures import (
    TaskFutureManager,
)

# Export WorkerPool and related types
from async_task_worker.pool import (
    TaskStatusCallback,
    WorkerPool,
)

# Export task queue
from async_task_worker.queue import (
    QueueStats,
    TaskQueue,
)
from async_task_worker.registry import (
    get_all_task_types,
    get_task_function,
    register_task,
    task,
)

# Export task status module
from async_task_worker.status import (
    TaskInfo,
    TaskStatus,
)

# Define what gets imported with `from async_task_worker import *`
__all__ = [
    # Main class
    "AsyncTaskWorker",
    # WorkerPool
    "WorkerPool",
    "TaskStatusCallback",
    # Task status and info
    "TaskInfo",
    "TaskStatus",
    # Task execution
    "ProgressCallback",
    "TaskExecutor",
    # Task registration
    "task",
    "register_task",
    "get_task_function",
    "get_all_task_types",
    # Caching
    "AsyncCacheAdapter",
    # Error handling
    "ErrorCategory",
    "TaskError",
    "TaskDefinitionError",
    "TaskExecutionError",
    "TaskTimeoutError",
    "TaskCancellationError",
    "ErrorHandler",
    # Futures
    "TaskFutureManager",
    # Queue
    "TaskQueue",
    "QueueStats",
]
