# AsyncTaskWorker

A robust asynchronous task worker system for Python applications that need to run background tasks efficiently.

## Features

- **Concurrent Task Processing**: Run multiple asynchronous tasks concurrently
- **Task Prioritization**: Assign priorities to tasks
- **Status Tracking**: Monitor task status, progress, and results
- **Cancellation Support**: Cancel running or pending tasks
- **Progress Reporting**: Real-time progress updates
- **Timeout Handling**: Configure timeouts for tasks
- **Task Registry**: Register task handlers by type

## How It Works

### Task Worker

The `AsyncTaskWorker` manages a pool of worker coroutines that process tasks from a priority queue:

1. When you add a task, it's placed in a priority queue
2. Worker coroutines pick tasks from the queue based on priority
3. The worker executes the task function and captures results or errors
4. Task status and progress are tracked in a `TaskInfo` object

```python
import asyncio
from task_worker import AsyncTaskWorker

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

### Task Registry

The task registry provides a way to register and retrieve task functions by type:

```python
from task_worker import task, get_task_function

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
from task_worker import task

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

### Using AsyncTaskWorker as a Daemon

To use the worker as a headless daemon in your application:

```python
import asyncio
import signal
from task_worker import AsyncTaskWorker, task

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

## API Reference

### AsyncTaskWorker

- `__init__(max_workers=10, task_timeout=None)`: Initialize worker pool
- `start()`: Start the worker pool
- `stop(timeout=5.0)`: Stop the worker pool
- `add_task(task_func, *args, priority=0, task_id=None, metadata=None, **kwargs)`: Add a task to the queue
- `get_task_info(task_id)`: Get information about a task
- `get_all_tasks(status=None, limit=None, older_than=None)`: Get filtered list of tasks
- `cancel_task(task_id)`: Cancel a running or pending task

### Task Registry

- `@task(task_type)`: Decorator to register a task function
- `register_task(task_type, task_func)`: Manually register a task function
- `get_task_function(task_type)`: Get the function for a task type
- `get_all_task_types()`: Get all registered task types

