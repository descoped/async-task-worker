# Async Task Worker

A robust asynchronous task worker system for Python applications with built-in caching support. This library provides a flexible framework for executing and managing background tasks with features like priority scheduling, progress tracking, and result caching.

## Installation

```bash
pip install async-task-worker
```

## Features

- **Asynchronous Execution**: Built on Python's asyncio for efficient concurrent task processing
- **Priority Queuing**: Tasks can be assigned priorities for execution order control
- **Task Tracking**: Comprehensive status monitoring and progress reporting
- **Cancellation Support**: Ability to cancel running or pending tasks
- **Result Caching**: Optional caching system with time-to-live (TTL) and size limits
- **Progress Reporting**: Built-in callback mechanism for task progress updates
- **Flexible Design**: Customizable cache adapters and task registration
- **Concurrent Task Processing**: Run multiple asynchronous tasks concurrently
- **Status Tracking**: Monitor task status, progress, and results
- **Task Registry**: Register task handlers by type
- **Worker Pool Architecture**: Decoupled worker pool implementation for improved flexibility
- **API Integration**: Ready-to-use FastAPI router for task management
- **Modular Structure**: Clean, consistent module organization with a logical structure

## Sequence Diagrams

- [Task Registry Sequence Diagram](https://github.com/descoped/async-task-worker/blob/master/docs/Task%20Registry%20Sequence%20Diagram.mmd)
- [AsyncTaskWorker Sequence Diagram](https://github.com/descoped/async-task-worker/blob/master/docs/AsyncTaskWorker%20Sequence%20Diagram.mmd)
- [Task Cache Sequence Diagram](https://github.com/descoped/async-task-worker/blob/master/docs/Task%20Cache%20Sequence%20Diagram.mmd)
- [Task Worker API Sequence Diagram](https://github.com/descoped/async-task-worker/blob/master/docs/Task%20Worker%20API%20Sequence%20Diagram.mmd)
- [AsyncTaskWorker Class Diagram](https://github.com/descoped/async-task-worker/blob/master/docs/AsyncTaskWorker%20Class%20Diagram.mmd)


## How It Works

### Task Worker Architecture

The system follows a modular, decoupled design:

1. **AsyncTaskWorker**: Main coordinator that integrates all components
2. **WorkerPool**: Handles worker lifecycle and task execution processing
3. **TaskQueue**: Manages prioritized task queuing and retrieval
4. **TaskExecutor**: Executes individual tasks with timeout and caching
5. **TaskRegistry**: Provides a global registry for task handlers
6. **AsyncCacheAdapter**: Provides integration with the async_cache package for efficient result caching

The workflow proceeds as follows:

1. When you add a task via `AsyncTaskWorker.add_task()`, it's placed in a priority queue
2. Worker coroutines from the `WorkerPool` pick tasks from the queue based on priority
3. The `TaskExecutor` executes the task function and captures results or errors
4. Task status and progress are tracked in a `TaskInfo` object maintained by the `AsyncTaskWorker`
5. Progress updates can be reported back via callbacks
6. Results can optionally be cached for future reuse

```python
import asyncio
from async_task_worker import AsyncTaskWorker


async def main():
    # Create worker with 3 concurrent workers and 60s timeout
    worker = AsyncTaskWorker(max_workers=3, task_timeout=60)
    await worker.start()

    try:
        # Use the worker...
        pass
    finally:
        # Graceful shutdown
        await worker.stop()


asyncio.run(main())
```

## Quick Start

```python
import asyncio
from async_task_worker import AsyncTaskWorker, task


# Define a task with the decorator
@task("process_data")
async def process_data(data, progress_callback=None):
   total = len(data)
   result = []

   for i, item in enumerate(data):
      # Process item
      processed = item * 2
      result.append(processed)

      # Report progress (0.0 to 1.0)
      if progress_callback:
         progress_callback((i + 1) / total)

      # Simulate work
      await asyncio.sleep(0.1)

   return result


async def main():
   # Create worker with 5 concurrent workers
   worker = AsyncTaskWorker(max_workers=5)

   # Start the worker pool
   await worker.start()

   try:
      # Add a task by referencing the function directly
      task_id = await worker.add_task(
         process_data,
         [1, 2, 3, 4, 5],
         priority=0
      )

      # Monitor task progress
      while True:
         info = worker.get_task_info(task_id)
         print(f"Progress: {info.progress * 100:.1f}%")

         if info.status not in ("pending", "running"):
            break

         await asyncio.sleep(0.5)

      # Get task result
      info = worker.get_task_info(task_id)
      if info.status == "completed":
         print(f"Result: {info.result}")
      else:
         print(f"Task failed: {info.error}")

   finally:
      # Stop the worker pool
      await worker.stop()


if __name__ == "__main__":
   asyncio.run(main())
```

## Detailed Documentation

### Core Components

#### Task Registration and Execution

There are two ways to work with tasks:

1. **Direct Function References**: Pass the function object directly to `add_task`
2. **Task Type References**: Use the task registry with task type names

##### Method 1: Direct Function References

```python
# Define and register a task
@task("process_data")
async def process_data(data, progress_callback=None):
    # Task implementation
    return result

# Execute by passing the function directly
task_id = await worker.add_task(process_data, data)
```

##### Method 2: Task Type References

```python
# Define and register a task
@task("process_data")
async def process_data(data, progress_callback=None):
   # Task implementation
   return result


# Get the function from the registry by task type name
from async_task_worker import get_task_function

task_func = get_task_function("process_data")

# Execute the task
task_id = await worker.add_task(task_func, data)
```

The second method is particularly useful when:
- Tasks are registered in different modules
- You want to decouple task execution from task implementation
- You're building a dynamic system where task types are determined at runtime

### Task Registry

The task registry provides a way to register and retrieve task functions by type:

```python
from async_task_worker import task, get_task_function


# Register a task function using decorator
@task("process_data")
async def process_data_task(data):
    # Process data
    return {"processed": True}


# Get a registered task function
task_func = get_task_function("process_data")
```

### Task Functions and Progress Reporting

Task functions must be async and can accept a progress_callback:

```python
from async_task_worker import task


@task("process_items")
async def process_items(items, progress_callback=None):
    total = len(items)
    results = []

    for i, item in enumerate(items):
        # Do work
        result = await process_item(item)
        results.append(result)

        # Report progress (0.0 to 1.0)
        if progress_callback:
            progress_callback((i + 1) / total)

    return results
```

#### Task Worker Configuration

The `AsyncTaskWorker` class manages all aspects of task execution:

```python
worker = AsyncTaskWorker(
    max_workers=10,              # Maximum number of concurrent tasks
    task_timeout=None,           # Default timeout for tasks in seconds (None = no timeout)
    worker_poll_interval=1.0,    # How frequently workers check for new tasks (seconds)
    cache_enabled=False,         # Whether to enable result caching
    cache_ttl=3600,              # Default cache TTL in seconds (1 hour)
    cache_max_size=1000,         # Maximum cache entries (LRU eviction)
    cache_adapter=None           # Custom cache adapter (defaults to in-memory)
)
```

#### Task Registration

The `@task` decorator registers a function in the global task registry:

```python
from async_task_worker import task


@task("unique_task_type")
async def my_task(arg1, arg2, progress_callback=None):
   """
   Task implementation.
   
   Args:
       arg1: First argument
       arg2: Second argument
       progress_callback: Optional callback for reporting progress
       
   Returns:
       The task result
   """
   # Initialization

   # Report 50% progress
   if progress_callback:
      progress_callback(0.5)

   # Perform task work

   return result
```

Key points about task functions:
- Must be **async** functions
- Can accept a `progress_callback` parameter for reporting progress
- Can return any result that will be stored in the task info
- Can raise exceptions which will be captured and reported

#### Task Status Lifecycle

Tasks go through the following states:
- **PENDING**: Task is queued but not yet running
- **RUNNING**: Task is currently executing
- **COMPLETED**: Task finished successfully
- **FAILED**: Task raised an exception
- **CANCELLED**: Task was explicitly cancelled

### Adding and Executing Tasks

When you add a task to the worker:

1. A unique task ID is generated (or you can provide one)
2. The task is added to the queue with its priority
3. A worker picks up the task when available
4. You can monitor the task's status and progress
5. When complete, you can access the result or error

```python
# Add a task
task_id = await worker.add_task(
    process_items,  # Task function
    ["item1", "item2", "item3"],  # Args
    priority=1,  # Lower number = higher priority
    metadata={"description": "Processing batch"}  # Optional metadata
)

# Check status
task_info = worker.get_task_info(task_id)
print(f"Status: {task_info.status}, Progress: {task_info.progress:.0%}")

# Get result when complete
if task_info.status == "completed":
    result = task_info.result
```

### Task Management

#### Adding Tasks

```python
# Basic usage
task_id = await worker.add_task(my_task, arg1, arg2)

# With all options
task_id = await worker.add_task(
    my_task,                   # Task function or get_task_function("task_type")
    arg1, arg2,                # Positional arguments passed to task
    kw1=val1, kw2=val2,        # Keyword arguments passed to task
    priority=0,                # Task priority (lower number = higher priority)
    task_id="custom-id",       # Custom task ID (default: auto-generated UUID)
    metadata={"key": "value"}, # Custom metadata to store with task
    timeout=30,                # Task-specific timeout in seconds
    use_cache=True,            # Whether to use cache for this task
    cache_ttl=60               # Custom TTL for this task's cache entry
)
```

#### Tracking Tasks

```python
# Get information about a specific task
info = worker.get_task_info(task_id)
print(f"Task ID: {info.id}")
print(f"Status: {info.status}")  # 'pending', 'running', 'completed', 'failed', 'cancelled'
print(f"Progress: {info.progress * 100:.1f}%")
print(f"Created: {info.created_at}")
print(f"Started: {info.started_at}")
print(f"Completed: {info.completed_at}")
print(f"Result: {info.result}")  # Only for completed tasks
print(f"Error: {info.error}")  # Only for failed tasks
print(f"Metadata: {info.metadata}")

# Get multiple tasks with filtering
from datetime import timedelta
from async_task_worker import TaskStatus

tasks = worker.get_all_tasks(
   status=TaskStatus.COMPLETED,  # Filter by status
   limit=10,  # Maximum number to return
   older_than=timedelta(hours=1)  # Filter by age
)

# Tasks are sorted by creation time (newest first)
for task in tasks:
   print(f"{task.id}: {task.status}")
```

#### Task Cancellation

```python
# Cancel a task (running or pending)
cancelled = await worker.cancel_task(task_id)

if cancelled:
    print("Task was cancelled successfully")
else:
    print("Task could not be cancelled (not found or already finished)")
```

#### Waiting for Tasks

The worker provides ways to wait for task completion:

```python
# Get a future that resolves when the task completes
future = worker.get_task_future(task_id)

try:
    # Will raise exception if task fails
    result = await future
    print(f"Task completed with result: {result}")
except asyncio.CancelledError:
    print("Task was cancelled")
except Exception as e:
    print(f"Task failed: {str(e)}")

# Wait for multiple tasks
futures = worker.get_task_futures([task_id1, task_id2])
results = await asyncio.gather(*futures, return_exceptions=True)

for i, result in enumerate(results):
    if isinstance(result, Exception):
        print(f"Task {i} failed: {result}")
    else:
        print(f"Task {i} result: {result}")
```

### Context Manager Support

The worker can be used as an async context manager:

```python
async def main():
    async with AsyncTaskWorker(max_workers=5) as worker:
        # Worker is automatically started
        task_id = await worker.add_task(my_task, arg1, arg2)
        # ...
        # Worker is automatically stopped at the end of context
```

### Using AsyncTaskWorker as a Daemon

To use the worker as a headless daemon in your application:

```python
import asyncio
import signal
from async_task_worker import AsyncTaskWorker, task

# Global worker
worker = None


@task("background_task")
async def background_task(data, progress_callback=None):
    # Long-running task implementation
    return {"status": "completed"}


async def start_worker():
    global worker
    worker = AsyncTaskWorker(max_workers=5)
    await worker.start()
    print("Worker started")


async def stop_worker():
    if worker:
        await worker.stop()
        print("Worker stopped")


async def main():
    # Handle signals for graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(stop_worker()))

    # Start worker
    await start_worker()

    # Add tasks as needed
    task_id = await worker.add_task(background_task, {"key": "value"})

    # Keep the daemon running
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await stop_worker()


if __name__ == "__main__":
    asyncio.run(main())
```

## Caching System

### Caching Overview

The task worker includes an optional caching system that stores task results to avoid redundant computation. This can dramatically improve performance for frequently executed tasks with the same arguments.

#### How Caching Works

1. **Flexible Cache Key Generation**: A unique key is created for each task based on:
   - Task function name
   - Positional arguments
   - Keyword arguments
   - Optional task metadata
   - User-defined custom key functions

2. **Lookup Process**:
   - Before executing a task, the worker checks if the result is already in cache
   - If found and not expired, the cached result is returned immediately
   - If not found, the task is executed and the result is stored in cache

3. **Eviction Policies**:
   - **TTL (Time-To-Live)**: Entries can expire after a set time
   - **LRU (Least Recently Used)**: When the cache size limit is reached, the least recently accessed entries are removed

### Cache Configuration

#### Global Cache Settings

```python
# Enable caching with default settings
worker = AsyncTaskWorker(
    cache_enabled=True,        # Enable the cache
    cache_ttl=3600,            # Default time-to-live: 1 hour (in seconds)
    cache_max_size=1000,       # Maximum number of entries
    cache_cleanup_interval=900 # Cleanup interval for stale mappings (15 min)
)

# Disable cache globally but allow per-task override
worker = AsyncTaskWorker(
    cache_enabled=False        # Globally disabled
)

# Disable automatic cleanup of stale mappings
worker = AsyncTaskWorker(
    cache_enabled=True,
    cleanup_interval=0         # Disable automatic cleanup
)
```

#### Per-Task Cache Options

```python
# Override global cache settings for specific task
task_id = await worker.add_task(
    my_task,
    arg1,
    arg2,
    use_cache=True,           # Enable caching for this task
    cache_ttl=120,            # Custom TTL: 2 minutes
    metadata={"version": "1.2"} # Optional metadata for key generation
)

# Bypass cache for a specific task even if globally enabled
task_id = await worker.add_task(
    my_task,
    arg1,
    arg2,
    use_cache=False           # Disable caching for this task
)

# Use a custom cache key function
task_id = await worker.add_task(
    my_task,
    arg1, 
    arg2,
    use_cache=True,
    cache_key_fn=my_custom_key_function  # Custom cache key generator
)
```

### Using the Cache Adapter

AsyncTaskWorker uses `AsyncCacheAdapter` which provides a clean integration with the `async_cache` package, enabling efficient caching with flexible key generation and storage strategies:

```python
from async_task_worker import AsyncCacheAdapter
from async_cache.adapters import RedisCacheAdapter

# Using Redis as a cache backend
import redis.asyncio as redis

redis_client = redis.Redis(host='localhost', port=6379, db=0)
redis_adapter = RedisCacheAdapter(redis_client)

cache_adapter = AsyncCacheAdapter(
    default_ttl=3600,          # Default TTL: 1 hour
    enabled=True,              # Enable caching
    max_serialized_size=5_242_880,  # 5MB max serialized size
    validate_keys=True,        # Validate cache keys
    cleanup_interval=900,      # Cleanup every 15 minutes
    adapter=redis_adapter      # Use Redis adapter
)

# Then pass to AsyncTaskWorker
worker = AsyncTaskWorker(
    max_workers=5,
    cache_adapter=cache_adapter,
    cache_enabled=True
)
```

### Customizable Cache Key Generation

The caching system supports flexible, composable cache key functions for advanced caching scenarios.

#### Context Dictionary

Each cache key function receives a context dictionary containing:

```python
context = {
    "func_name": "task_function_name",
    "args": (arg1, arg2, ...),  # Positional arguments
    "kwargs": {"key1": value1, ...},  # Keyword arguments
    "entry_id": "unique-id",  # Optional task/entry ID
    "metadata": {"version": "1.0", ...}  # Optional metadata
}
```

#### Basic Custom Key Function

You can create a custom key function to control exactly how cache keys are generated:

```python
def version_based_key(context):
    """Generate a cache key based on function name and version."""
    func = context["func_name"]
    version = context.get("metadata", {}).get("version", "1.0")
    return f"{func}:v{version}"

# Use the custom key function
task_id = await worker.add_task(
    process_data,
    dataset,
    use_cache=True,
    metadata={"version": "2.0"},
    cache_key_fn=version_based_key
)
```

#### Composable Cache Keys

For complex caching scenarios, you can compose multiple key functions together:

```python
from async_cache import compose_key_functions, extract_key_component

# Create component extractors
user_id = extract_key_component("kwargs.user_id")
api_version = extract_key_component("metadata.version")
func_name = extract_key_component("func_name")

# Compose them into a single key function
composite_key = compose_key_functions(func_name, user_id, api_version)

# Use the composite key function 
task_id = await worker.add_task(
    get_user_data,
    user_id=123,
    use_cache=True,
    metadata={"version": "v2"},
    cache_key_fn=composite_key
)
# Will generate a key like: "get_user_data:123:v2"
```

#### Key Component Decorator

For better readability and reuse, you can use the decorator-based approach:

```python
from async_cache import key_component, compose_key_functions

# Create named components with decorators
@key_component("user")
def user_component(context):
    return str(context["kwargs"].get("user_id", "anonymous"))

@key_component("region")
def region_component(context):
    return context["kwargs"].get("region", "global")

@key_component()  # Uses function name as component name
def timestamp(context):
    return context.get("metadata", {}).get("timestamp", "0")

# Compose named components
composite_key = compose_key_functions(user_component, region_component, timestamp)

# Use in a task
task_id = await worker.add_task(
    get_regional_data,
    user_id=123,
    region="eu-west",
    use_cache=True,
    metadata={"timestamp": "2023-04-01"},
    cache_key_fn=composite_key
)
# Will generate a key like: "user:123:region:eu-west:timestamp:2023-04-01"
```

#### Cache Utilities

```python
# Temporarily disable the cache (context manager)
async with worker.cache._cache.temporarily_disabled():
    # Cache operations in this block will be skipped
    result = await worker.add_task(my_task, arg1, arg2, use_cache=True)
    # The cache is actually bypassed despite use_cache=True

# Get cache key for a specific task ID
key = await worker.cache.get_cache_key_for_task("task-123")

# Invalidate cache by task ID
await worker.cache.invalidate_by_task_id("task-123")

# Control the background cleanup task
await worker.cache.start_cleanup_task()  # Start periodic cleanup
await worker.cache.stop_cleanup_task()   # Stop periodic cleanup
```

### Cache Management

```python
# Invalidate a specific cache entry
invalidated = await worker.invalidate_cache(my_task, arg1, arg2)
if invalidated:
   print("Cache entry was removed")

# Invalidate using task type name
from async_task_worker import get_task_function

task_func = get_task_function("my_task_type")
invalidated = await worker.invalidate_cache(task_func, arg1, arg2)

# Clear the entire cache
await worker.clear_cache()
```

### Cache Adapters

The `async_cache` package provides several cache adapters that can be used with AsyncTaskWorker:

1. **MemoryCacheAdapter**: In-memory LRU cache (default)
2. **RedisCacheAdapter**: Redis-backed distributed cache

```python
# Using Memory Adapter (default)
from async_cache.adapters import MemoryCacheAdapter
from async_task_worker import AsyncCacheAdapter

memory_adapter = MemoryCacheAdapter(max_size=10000)
cache_adapter = AsyncCacheAdapter(adapter=memory_adapter)

# Using Redis Adapter
from async_cache.adapters import RedisCacheAdapter
import redis.asyncio as redis

redis_client = redis.Redis(host='localhost', port=6379, db=0)
redis_adapter = RedisCacheAdapter(redis_client, 
                                 key_prefix="tasks:",
                                 serializer="msgpack")

cache_adapter = AsyncCacheAdapter(adapter=redis_adapter)

# Pass to AsyncTaskWorker
worker = AsyncTaskWorker(
   cache_enabled=True,
   cache_adapter=cache_adapter
)
```

### Real-World Examples

#### Version-based Cache Invalidation

```python
# Create a cache key function that includes API version
def versioned_cache_key(context):
    func = context["func_name"]
    version = context.get("metadata", {}).get("api_version", "v1")
    return f"{func}:{version}"

# User requests data with v1 API
task_id1 = await worker.add_task(
    get_user_data,
    user_id=123,
    use_cache=True,
    metadata={"api_version": "v1"},
    cache_key_fn=versioned_cache_key
)

# Later, after API update, same data with v2 API
# Will use a different cache key, not reusing v1 cached result
task_id2 = await worker.add_task(
    get_user_data,
    user_id=123,
    use_cache=True,
    metadata={"api_version": "v2"},
    cache_key_fn=versioned_cache_key
)
```

#### Multi-tenant Cache Isolation

```python
# Create a key function that isolates cache by tenant
tenant_key = compose_key_functions(
    extract_key_component("func_name"),
    extract_key_component("metadata.tenant_id"),
    extract_key_component("args.0")  # First arg value
)

# Different tenants share same function but get isolated cache
for tenant_id in ["tenant1", "tenant2", "tenant3"]:
    task_id = await worker.add_task(
        process_data,
        "sample_data",
        use_cache=True,
        metadata={"tenant_id": tenant_id},
        cache_key_fn=tenant_key
    )
```

### Automatic Cleanup of Stale Mappings

The cache system includes an automatic cleanup mechanism to prevent memory leaks from orphaned task ID mappings when cache entries expire through TTL:

```python
# Configure a worker with cleanup enabled
worker = AsyncTaskWorker(
    cache_enabled=True,
    cache_ttl=3600,            # 1 hour cache TTL
    cleanup_interval=900       # Clean up stale mappings every 15 minutes
)

# The sequence of events:
# 1. Add a task with caching enabled
task_id = await worker.add_task(
    process_data, 
    dataset,
    use_cache=True,
    cache_ttl=60               # 1 minute TTL
)

# 2. The task_id is mapped to its cache key in task_key_map

# 3. After 1 minute, the cache entry expires due to TTL

# 4. Without cleanup, the mapping would remain in memory indefinitely

# 5. With cleanup enabled, the background task periodically checks
#    all mappings and removes any where the cache entry is gone

# 6. Result: No memory leaks from expired cache entries
```

The cleanup task starts automatically with the worker and runs at the configured interval. It only removes mappings when the actual cache entries have expired or been removed, ensuring that all valid mappings are preserved.

## Advanced Usage

### Task Registry

The task registry provides ways to discover and lookup registered tasks:

```python
from async_task_worker import get_task_function, get_all_task_types

# Get all registered task types
task_types = get_all_task_types()
print(f"Available tasks: {task_types}")

# Get a task function by type
task_func = get_task_function("process_data")
if task_func:
   # Execute the task
   task_id = await worker.add_task(task_func, data)
else:
   print("Task type not found")
```

### Manual Task Registration

Besides the decorator, you can manually register tasks:

```python
from async_task_worker import register_task


async def my_task(data):
   # Task implementation
   return result


# Register the task
register_task("custom_task", my_task)

# Later, retrieve and execute
task_func = get_task_function("custom_task")
task_id = await worker.add_task(task_func, data)
```

### Progress Reporting

Tasks can report progress through the `progress_callback` parameter:

```python
@task("long_running_task")
async def long_running_task(items, progress_callback=None):
    total = len(items)
    
    for i, item in enumerate(items):
        # Process item
        
        # Report progress as a value between 0.0 and 1.0
        if progress_callback:
            progress_callback(i / total)
        
        await asyncio.sleep(0.1)
    
    # Final progress update
    if progress_callback:
        progress_callback(1.0)
        
    return "Completed"
```

The progress is stored in the task info and can be retrieved with `get_task_info()`.

## Error Handling

The task worker captures exceptions raised by tasks:

```python
@task("might_fail")
async def might_fail(value):
    if value < 0:
        raise ValueError("Value cannot be negative")
    return value * 2

# Execute task that will fail
task_id = await worker.add_task(might_fail, -5)

# Check the result
info = worker.get_task_info(task_id)
assert info.status == TaskStatus.FAILED
print(f"Task failed: {info.error}")  # "Value cannot be negative"
```

## API Reference

### AsyncTaskWorker

- `__init__(max_workers=10, task_timeout=None, cache_adapter=None, cache_enabled=False, cache_ttl=3600)`: Initialize worker pool
- `start()`: Start the worker pool
- `stop(timeout=5.0)`: Stop the worker pool
- `add_task(task_func, *args, priority=0, task_id=None, metadata=None, use_cache=None, cache_ttl=None, cache_key_fn=None, **kwargs)`: Add a task to the queue
- `get_task_info(task_id)`: Get information about a task
- `get_all_tasks(status=None, limit=None, older_than=None)`: Get filtered list of tasks
- `cancel_task(task_id)`: Cancel a running or pending task
- `get_task_future(task_id)`: Get a future that resolves when the task completes
- `wait_for_tasks(task_ids, timeout=None)`: Wait for multiple tasks to complete
- `wait_for_any_task(task_ids, timeout=None)`: Wait for any of the specified tasks to complete
- `invalidate_cache(task_func, *args, cache_key_fn=None, **kwargs)`: Invalidate a specific cache entry
- `clear_cache()`: Clear all cache entries

### AsyncCacheAdapter

- `__init__(default_ttl=None, enabled=True, max_serialized_size=10485760, validate_keys=False, cleanup_interval=900, max_size=1000)`: Initialize the cache adapter
- `get(func_name, args, kwargs, cache_key_fn=None, task_id=None, metadata=None)`: Get cached result
- `set(func_name, args, kwargs, result, ttl=None, cache_key_fn=None, task_id=None, metadata=None)`: Store result in cache
- `invalidate(func_name, args, kwargs, cache_key_fn=None, task_id=None, metadata=None)`: Invalidate specific cache entry
- `invalidate_by_task_id(task_id)`: Invalidate by task ID
- `clear()`: Clear the entire cache
- `start_cleanup_task()`: Start the periodic cleanup task
- `stop_cleanup_task()`: Stop the periodic cleanup task
- `enabled`: Property to get/set if cache is enabled
- `default_ttl`: Property to get/set default TTL
- `cleanup_interval`: Property to get/set cleanup interval

### WorkerPool

- `__init__(task_queue, task_executor, max_workers=10)`: Initialize the worker pool
- `start()`: Start the worker pool
- `stop(timeout=5.0)`: Stop the worker pool gracefully
- `cancel_running_task(task_id)`: Cancel a task that is currently running in a worker

### Task Registry

- `@task(task_type)`: Decorator to register a task function
- `register_task(task_type, task_func)`: Manually register a task function
- `get_task_function(task_type)`: Get the function for a task type
- `get_all_task_types()`: Get all registered task types

### API Router

- `create_task_worker_router(worker, prefix="", tags=None)`: Create a FastAPI router for task management

## API Router - Control API for Task Worker (optional)

The AsyncTaskWorker library includes a FastAPI router that you can integrate into your existing FastAPI applications to expose the task worker functionality via a RESTful API.

### Install requirement

```bash
pip install fastapi
```

### Quick Start

```python
from fastapi import FastAPI
from contextlib import asynccontextmanager
from async_task_worker import AsyncTaskWorker, create_task_worker_router

# Create task worker
worker = AsyncTaskWorker(max_workers=10, cache_enabled=True)


# Define application lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
   await worker.start()  # Start worker when app starts
   yield
   await worker.stop()  # Stop worker when app shuts down


# Create FastAPI app with lifespan
app = FastAPI(lifespan=lifespan)

# Create and include the task worker router
app.include_router(create_task_worker_router(worker))
```

### Router Customization

You can customize the router with the following options:

```python
task_router = create_task_worker_router(
    worker,
    prefix="/api/v1",  # Add a URL prefix to all routes
    tags=["background-tasks"]  # Custom OpenAPI documentation tags
)
```

### Available Endpoints

The router provides the following endpoints:

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/tasks` | Submit a new task for execution |
| GET | `/tasks/{task_id}` | Get status and results of a specific task |
| DELETE | `/tasks/{task_id}` | Cancel a running or pending task |
| GET | `/tasks` | List tasks with optional filtering |
| GET | `/tasks/types` | Get a list of all registered task types |
| GET | `/health` | Check the health of the task worker service |

### Example: Submitting a Task

```python
import httpx

async def submit_task():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/tasks",
            json={
                "task_type": "process_data",
                "params": {
                    "data_id": "123",
                    "options": {"normalize": True}
                },
                "priority": 1
            }
        )
        
        task_data = response.json()
        task_id = task_data["id"]
        return task_id
```

### Example: Checking Task Status

```python
async def check_task(task_id):
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://localhost:8000/tasks/{task_id}")
        
        if response.status_code == 200:
            task_data = response.json()
            status = task_data["status"]
            
            if status == "completed":
                return task_data["result"]
            elif status == "failed":
                raise RuntimeError(f"Task failed: {task_data['error']}")
            else:
                return f"Task is {status} ({task_data['progress']*100:.1f}% complete)"
```

## License

MIT

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

### Build

```bash
git clone https://github.com/descoped/async-task-worker.git
cd async-task-worker
uv venv
source .venv/bin/activate
uv sync --all-groups
code .
```
