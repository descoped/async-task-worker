"""
Async Task Worker
A robust asynchronous task worker system for Python applications.
"""
# Export main AsyncTaskWorker class
from async_task_worker.async_task_worker import (
    AsyncTaskWorker,
)
# Export error handling classes
from async_task_worker.error_handler import (
    ErrorCategory,
    TaskError,
    TaskDefinitionError,
    TaskExecutionError,
    TaskTimeoutError,
    TaskCancellationError,
    ErrorHandler,
)
# Export cache-related classes
from async_task_worker.task_cache import (
    CacheAdapter,
    MemoryCacheAdapter,
    TaskCache,
)
# Export task executor
from async_task_worker.task_executor import (
    ProgressCallback,
    TaskExecutor,
)
# Export task future functionality
from async_task_worker.task_futures import (
    TaskFutureManager,
)
# Export task queue
from async_task_worker.task_queue import (
    TaskQueue,
    QueueStats,
)
from async_task_worker.task_registry import (
    task,
    register_task,
    get_task_function,
    get_all_task_types,
)
# Export task status module
from async_task_worker.task_status import (
    TaskStatus,
    TaskInfo,
)
# Export WorkerPool and related types
from async_task_worker.worker_pool import (
    WorkerPool,
    TaskStatusCallback,
)

# Define what gets imported with `from async_task_worker import *`
__all__ = [
    # Main class
    'AsyncTaskWorker',

    # WorkerPool 
    'WorkerPool',
    'TaskStatusCallback',

    # Task status and info
    'TaskInfo',
    'TaskStatus',

    # Task execution
    'ProgressCallback',
    'TaskExecutor',

    # Task registration
    'task',
    'register_task',
    'get_task_function',
    'get_all_task_types',

    # Caching
    'CacheAdapter',
    'MemoryCacheAdapter',
    'TaskCache',

    # Error handling
    'ErrorCategory',
    'TaskError',
    'TaskDefinitionError',
    'TaskExecutionError',
    'TaskTimeoutError',
    'TaskCancellationError',
    'ErrorHandler',

    # Futures
    'TaskFutureManager',

    # Queue
    'TaskQueue',
    'QueueStats',
]
