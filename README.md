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

## Quick Start

```python
import asyncio
from task_worker import AsyncTaskWorker, task

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
from task_worker import get_task_function
task_func = get_task_function("process_data")

# Execute the task
task_id = await worker.add_task(task_func, data)
```

The second method is particularly useful when:
- Tasks are registered in different modules
- You want to decouple task execution from task implementation
- You're building a dynamic system where task types are determined at runtime

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
from task_worker import task

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
print(f"Result: {info.result}")   # Only for completed tasks
print(f"Error: {info.error}")     # Only for failed tasks
print(f"Metadata: {info.metadata}")

# Get multiple tasks with filtering
from datetime import timedelta
from task_worker import TaskStatus

tasks = worker.get_all_tasks(
    status=TaskStatus.COMPLETED,       # Filter by status
    limit=10,                         # Maximum number to return
    older_than=timedelta(hours=1)     # Filter by age
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

## Caching System

### Caching Overview

The task worker includes an optional caching system that stores task results to avoid redundant computation. This can dramatically improve performance for frequently executed tasks with the same arguments.

#### How Caching Works

1. **Cache Key Generation**: A unique key is created for each task based on:
   - Task function name
   - Positional arguments
   - Keyword arguments

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
    cache_max_size=1000        # Maximum number of entries
)

# Disable cache globally but allow per-task override
worker = AsyncTaskWorker(
    cache_enabled=False        # Globally disabled
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
    cache_ttl=120             # Custom TTL: 2 minutes
)

# Bypass cache for a specific task even if globally enabled
task_id = await worker.add_task(
    my_task,
    arg1,
    arg2,
    use_cache=False           # Disable caching for this task
)
```

### Cache Management

```python
# Invalidate a specific cache entry
invalidated = await worker.invalidate_cache(my_task, arg1, arg2)
if invalidated:
    print("Cache entry was removed")

# Invalidate using task type name
from task_worker import get_task_function
task_func = get_task_function("my_task_type")
invalidated = await worker.invalidate_cache(task_func, arg1, arg2)

# Clear the entire cache
await worker.clear_cache()
```

### Custom Cache Adapters

By default, the task worker uses an in-memory cache, but you can create custom cache adapters for persistent storage or distributed caching.

```python
from task_worker import CacheAdapter
from typing import Any, Optional, Tuple

class RedisCache(CacheAdapter):
    """Example Redis-based cache adapter"""
    
    def __init__(self, redis_client):
        self.redis = redis_client
        
    async def get(self, key: str) -> Tuple[bool, Any]:
        """Get a value from the cache"""
        value = await self.redis.get(key)
        if value is None:
            return False, None
        # Deserialize value (e.g., from JSON or pickle)
        deserialized = self._deserialize(value)
        return True, deserialized
        
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set a value in the cache"""
        # Serialize value (e.g., to JSON or pickle)
        serialized = self._serialize(value)
        await self.redis.set(key, serialized, ex=ttl)
        
    async def delete(self, key: str) -> bool:
        """Delete a value from the cache"""
        result = await self.redis.delete(key)
        return result > 0
        
    async def clear(self) -> None:
        """Clear all items (matching a pattern)"""
        # Implementation depends on your Redis usage pattern
        # For example, using a prefix for all keys
        keys = await self.redis.keys("task_cache:*")
        if keys:
            await self.redis.delete(*keys)
            
    def _serialize(self, value):
        # Implementation
        pass
        
    def _deserialize(self, data):
        # Implementation
        pass
        
# Use custom adapter
import redis.asyncio as redis
redis_client = redis.Redis(host='localhost', port=6379, db=0)

worker = AsyncTaskWorker(
    cache_enabled=True,
    cache_adapter=RedisCache(redis_client)
)
```

## Advanced Usage

### Task Registry

The task registry provides ways to discover and lookup registered tasks:

```python
from task_worker import get_task_function, get_all_task_types

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
from task_worker import register_task

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


## API Router - Control API for Task Worker

The AsyncTaskWorker library includes a FastAPI router that you can integrate into your existing FastAPI applications to expose the task worker functionality via a RESTful API.

### Quick Start

```python
from fastapi import FastAPI
from contextlib import asynccontextmanager
from task_worker.task_worker_api import AsyncTaskWorker, create_task_worker_router

# Create task worker
worker = AsyncTaskWorker(max_workers=10, cache_enabled=True)

# Define application lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    await worker.start()  # Start worker when app starts
    yield
    await worker.stop()   # Stop worker when app shuts down

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
