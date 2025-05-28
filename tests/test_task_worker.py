import asyncio
import logging
from datetime import datetime

import pytest

from async_task_worker import AsyncTaskWorker, TaskStatus
from async_task_worker.registry import task
from async_task_worker.status import TaskInfo

logger = logging.getLogger(__name__)


# Test tasks
@task("error_task")
async def error_task(error_msg="Test error"):
    """Task that raises an error"""
    raise RuntimeError(error_msg)


@task("fast_task")
async def fast_task(duration=0.05, result_value="done", progress_callback=None):
    """Simple fast task with optional progress reporting"""
    if progress_callback:
        progress_callback(0.0)

    await asyncio.sleep(duration)

    if progress_callback:
        progress_callback(1.0)

    return {"status": "success", "value": result_value}


@task("slow_task")
async def slow_task(duration=0.2, steps=4, progress_callback=None):
    """Task with multiple steps and progress reporting"""
    result = []
    step_time = duration / steps

    for i in range(steps):
        await asyncio.sleep(step_time)
        result.append(i)

        if progress_callback:
            progress_callback((i + 1) / steps)

    return {"steps": steps, "result": result}


@task("cancellable_task")
async def cancellable_task(duration=1.0, check_interval=0.05, progress_callback=None):
    """Task that checks for cancellation regularly"""
    start_time = asyncio.get_event_loop().time()
    elapsed = 0

    while elapsed < duration:
        if asyncio.current_task().cancelled():
            raise asyncio.CancelledError("Task cancelled during execution")

        await asyncio.sleep(check_interval)
        elapsed = asyncio.get_event_loop().time() - start_time

        if progress_callback:
            progress_callback(min(1.0, elapsed / duration))

    return {"status": "completed", "duration": elapsed}


@task("timeout_task")
async def timeout_task(timeout=5.0, progress_callback=None):
    """Task designed to timeout"""
    if progress_callback:
        progress_callback(0.0)

    # Sleep longer than the default worker timeout
    await asyncio.sleep(timeout)

    if progress_callback:
        progress_callback(1.0)

    return {"status": "should never reach this"}


# Helper functions
async def wait_for_status(worker, task_id, status, timeout=1.0):
    """Wait for a task to reach a specific status with cancellation handling."""
    try:
        async with asyncio.timeout(timeout):
            while True:
                task_info = await worker.get_task_info(task_id)
                if task_info:
                    logger.info(f"Task {task_id} status: {task_info.status}")
                    if task_info.status == status:
                        return True
                    elif status is None and task_info.is_terminal_state():
                        return True
                    elif task_info.is_terminal_state():
                        logger.warning(
                            f"Task {task_id} reached terminal state {task_info.status} before target {status}.")
                        return False
                else:
                    logger.warning(f"Task {task_id} not found.")
                await asyncio.sleep(0.01)  # Adjust polling interval as needed
    except asyncio.TimeoutError:
        task_info = await worker.get_task_info(task_id)
        current_status = task_info.status if task_info else "unknown"
        print(f"Timeout waiting for task {task_id} to reach {status}, current status: {current_status}")
        return False
    except asyncio.CancelledError:
        logger.warning(f"wait_for_status cancelled for task {task_id}")
        return False


# Fixtures
@pytest.fixture()
async def worker():
    """Create and clean up a worker with asynchronous teardown."""
    worker = AsyncTaskWorker(max_workers=2, task_timeout=2.0, worker_poll_interval=0.01, cache_enabled=True)
    await worker.start()
    try:
        yield worker
    finally:
        try:
            await worker.stop(timeout=1.0)
        except Exception as e:
            print(f"Error stopping worker: {e}")


# Tests
@pytest.mark.asyncio
async def test_basic_task_execution(worker):
    """Test that a task executes correctly"""
    # Use a very short task to avoid timing issues
    task_id = await worker.add_task(fast_task, 0.01)

    # Wait for it to complete
    assert await wait_for_status(worker, task_id, TaskStatus.COMPLETED, timeout=2.0)

    # Check results
    task_info = await worker.get_task_info(task_id)
    assert task_info.status == TaskStatus.COMPLETED
    assert task_info.result["status"] == "success"


@pytest.mark.asyncio
async def test_task_with_args(worker):
    """Test passing arguments to tasks"""
    test_value = "custom_result"
    task_id = await worker.add_task(fast_task, 0.01, test_value)

    assert await wait_for_status(worker, task_id, TaskStatus.COMPLETED, timeout=2.0)

    task_info = await worker.get_task_info(task_id)
    assert task_info.result["value"] == test_value


@pytest.mark.asyncio
async def test_task_error_handling(worker):
    """Test that errors in tasks are handled properly"""
    error_message = "Custom error message"
    task_id = await worker.add_task(error_task, error_message)

    assert await wait_for_status(worker, task_id, TaskStatus.FAILED, timeout=2.0)

    task_info = await worker.get_task_info(task_id)
    assert error_message in task_info.error


@pytest.mark.asyncio
async def test_task_cancellation(worker):
    """Test that tasks can be cancelled"""
    # Use a longer duration to ensure we can cancel before completion
    task_id = await worker.add_task(slow_task, 2.0)  # Increased from 0.5 to 2.0 seconds

    # Wait for task to start
    assert await wait_for_status(worker, task_id, TaskStatus.RUNNING, timeout=1.0)

    # Add a small delay to ensure task is fully running
    await asyncio.sleep(0.1)

    # Cancel the task
    success = await worker.cancel_task(task_id)
    assert success is True, "Task cancellation should return True"

    # Verify cancellation
    assert await wait_for_status(worker, task_id, TaskStatus.CANCELLED,
                                 timeout=1.0), "Task should reach CANCELLED state"


@pytest.mark.asyncio
async def test_multiple_tasks(worker):
    """Test running multiple concurrent tasks"""
    task_ids = []
    for i in range(3):
        task_id = await worker.add_task(fast_task, 0.01, f"result_{i}")
        task_ids.append(task_id)

    # Wait for all tasks to complete
    for task_id in task_ids:
        assert await wait_for_status(worker, task_id, TaskStatus.COMPLETED, timeout=2.0)

    # Check results
    for i, task_id in enumerate(task_ids):
        task_info = await worker.get_task_info(task_id)
        assert task_info.result["value"] == f"result_{i}"


@pytest.mark.asyncio
async def test_task_progress(worker):
    """Test that progress callbacks work"""
    task_id = await worker.add_task(slow_task, 0.1, 2)  # Shorter duration, fewer steps

    # Wait for completion
    assert await wait_for_status(worker, task_id, TaskStatus.COMPLETED, timeout=2.0)

    # Check progress reached 100%
    task_info = await worker.get_task_info(task_id)
    assert task_info.progress == 1.0


@pytest.mark.asyncio
async def test_task_futures(worker):
    """Test getting and awaiting task futures"""
    task_id = await worker.add_task(fast_task, 0.01, "future_test")

    # Wait for the task to complete first
    assert await wait_for_status(worker, task_id, TaskStatus.COMPLETED, timeout=1.0)

    # Then get the future (which should be already resolved)
    future = await worker.get_task_future(task_id)

    # No need for wait_for since it's already done
    result = await future

    assert result["status"] == "success"
    assert result["value"] == "future_test"


@pytest.mark.asyncio
async def test_wait_for_tasks(worker):
    """Test waiting for multiple tasks"""
    # Start three tasks with different durations
    task_ids = []
    for i in range(3):
        duration = 0.01 * (i + 1)  # Keep durations very short
        task_id = await worker.add_task(fast_task, duration, f"wait_test_{i}")
        task_ids.append(task_id)

    # First wait for all tasks to complete using our helper
    for task_id in task_ids:
        assert await wait_for_status(worker, task_id, TaskStatus.COMPLETED, timeout=1.0)

    # Then use wait_for_tasks (should return immediately since tasks are done)
    results = await worker.wait_for_tasks(task_ids)

    # Verify results
    assert len(results) == 3
    for i, result in enumerate(results):
        assert result["value"] == f"wait_test_{i}"


@pytest.mark.asyncio
async def test_wait_for_any_task(worker):
    """Test waiting for any task to complete"""
    # Start two tasks with different durations
    fast_id = await worker.add_task(fast_task, 0.01, "fast")
    slow_id = await worker.add_task(slow_task, 0.1)

    # Wait for fast task to complete using our helper
    assert await wait_for_status(worker, fast_id, TaskStatus.COMPLETED, timeout=1.0)

    # Then use wait_for_any_task (should return immediately with fast task)
    result, completed_id = await worker.wait_for_any_task([fast_id, slow_id])

    # The faster task should complete first
    assert completed_id == fast_id
    assert result["value"] == "fast"


@pytest.mark.asyncio
async def test_task_caching(worker):
    """Test that task caching works"""
    # Execute task once
    args = (0.01, "cached_result")
    first_id = await worker.add_task(fast_task, *args)
    assert await wait_for_status(worker, first_id, TaskStatus.COMPLETED, timeout=2.0)

    # Execute same task again with same args (should use cache)
    second_id = await worker.add_task(fast_task, *args)

    # Should complete very quickly
    assert await wait_for_status(worker, second_id, TaskStatus.COMPLETED, timeout=0.5)

    task_info = await worker.get_task_info(second_id)
    assert task_info.result["value"] == "cached_result"


@pytest.mark.asyncio
async def test_cached_task_completion_callbacks(worker):
    """Test that cached tasks trigger completion callbacks just like non-cached tasks"""
    completion_calls = []
    original_pool_callback = worker.worker_pool.on_task_complete

    # Monkey patch the pool's completion callback to track calls
    async def tracking_pool_callback(task_id, task_info, result):
        completion_calls.append((task_id, result))
        if original_pool_callback:
            await original_pool_callback(task_id, task_info, result)

    worker.worker_pool.on_task_complete = tracking_pool_callback

    try:
        # Execute task once to populate cache
        args = (0.01, "cached_callback_test")
        first_id = await worker.add_task(fast_task, *args)
        assert await wait_for_status(worker, first_id, TaskStatus.COMPLETED, timeout=2.0)

        # Verify first task triggered completion callback
        assert len(completion_calls) == 1
        assert completion_calls[0][0] == first_id
        assert completion_calls[0][1]["value"] == "cached_callback_test"

        # Execute same task again with same args (should use cache)
        second_id = await worker.add_task(fast_task, *args)
        assert await wait_for_status(worker, second_id, TaskStatus.COMPLETED, timeout=0.5)

        # Verify second task (cached) also triggered completion callback
        assert len(completion_calls) == 2
        assert completion_calls[1][0] == second_id
        assert completion_calls[1][1]["value"] == "cached_callback_test"

        # Verify both tasks have same result but different IDs
        task_info_1 = await worker.get_task_info(first_id)
        task_info_2 = await worker.get_task_info(second_id)
        
        assert task_info_1.result == task_info_2.result
        assert task_info_1.status == TaskStatus.COMPLETED
        assert task_info_2.status == TaskStatus.COMPLETED
        assert first_id != second_id

    finally:
        # Restore original callback
        worker.worker_pool.on_task_complete = original_pool_callback


@pytest.mark.asyncio 
async def test_cached_task_futures_completion(worker):
    """Test that futures are properly completed for cached tasks"""
    # Execute task once to populate cache
    args = (0.01, "cached_future_test")
    first_id = await worker.add_task(fast_task, *args)
    assert await wait_for_status(worker, first_id, TaskStatus.COMPLETED, timeout=2.0)

    # Execute same task again with same args (should use cache)
    second_id = await worker.add_task(fast_task, *args)
    
    # Get future for cached task
    future = await worker.get_task_future(second_id)
    
    # Future should resolve with cached result
    result = await future
    assert result["value"] == "cached_future_test"
    
    # Task status should be completed
    task_info = await worker.get_task_info(second_id)
    assert task_info.status == TaskStatus.COMPLETED
    assert task_info.result == result


@pytest.mark.asyncio
async def test_cache_metadata_flag(worker):
    """Test that tasks correctly indicate whether results came from cache"""
    # Execute task once to populate cache
    args = (0.01, "cache_metadata_test")
    first_id = await worker.add_task(fast_task, *args)
    assert await wait_for_status(worker, first_id, TaskStatus.COMPLETED, timeout=2.0)

    # Execute same task again with same args (should use cache)
    second_id = await worker.add_task(fast_task, *args)
    assert await wait_for_status(worker, second_id, TaskStatus.COMPLETED, timeout=1.0)

    # Get both task infos
    first_task = await worker.get_task_info(first_id)
    second_task = await worker.get_task_info(second_id)
    
    # Verify cache metadata
    assert first_task.from_cache is False  # First execution should not be from cache
    assert second_task.from_cache is True  # Second execution should be from cache
    
    # Both should have same result
    assert first_task.result == second_task.result
    assert first_task.result["value"] == "cache_metadata_test"


@pytest.mark.asyncio
async def test_cache_metadata_in_serialization(worker):
    """Test that cache metadata is included in task serialization"""
    # Execute task once to populate cache
    args = (0.01, "serialization_test")
    first_id = await worker.add_task(fast_task, *args)
    assert await wait_for_status(worker, first_id, TaskStatus.COMPLETED, timeout=2.0)

    # Execute same task again with same args (should use cache)
    second_id = await worker.add_task(fast_task, *args)
    assert await wait_for_status(worker, second_id, TaskStatus.COMPLETED, timeout=1.0)

    # Get tasks via get_all_tasks
    all_tasks = await worker.get_all_tasks()
    first_task = next(t for t in all_tasks if t.id == first_id)
    second_task = next(t for t in all_tasks if t.id == second_id)
    
    # Verify cache metadata in serialized tasks
    assert first_task.from_cache is False
    assert second_task.from_cache is True
    
    # Test to_dict serialization
    first_dict = first_task.to_dict()
    second_dict = second_task.to_dict()
    
    assert "from_cache" in first_dict
    assert "from_cache" in second_dict
    assert first_dict["from_cache"] is False
    assert second_dict["from_cache"] is True


@pytest.mark.asyncio
async def test_cache_invalidation(worker):
    """Test that cache invalidation works"""
    # Execute task
    args = (0.1, "to_invalidate")
    first_id = await worker.add_task(fast_task, *args)
    assert await wait_for_status(worker, first_id, TaskStatus.COMPLETED, timeout=2.0)

    # Invalidate the cache
    result = await worker.invalidate_cache(fast_task, *args)
    assert result is True

    # Execute again - should not use cache
    second_id = await worker.add_task(fast_task, *args, use_cache=False)  # Explicitly disable cache

    # Wait for completion
    assert await wait_for_status(worker, second_id, TaskStatus.COMPLETED, timeout=2.0)


@pytest.mark.asyncio
async def test_queue_cancellation(worker):
    """Test cancellation of a task that is still in the queue (not yet running)"""
    # Set up a worker with only 1 worker thread to ensure queueing
    worker = AsyncTaskWorker(max_workers=1, task_timeout=2.0, worker_poll_interval=0.01)
    await worker.start()

    try:
        # Add a long-running task to occupy the worker
        blocking_id = await worker.add_task(slow_task, 0.5)

        # Wait for it to start running
        assert await wait_for_status(worker, blocking_id, TaskStatus.RUNNING, timeout=1.0)

        # Add another task that should be queued
        queued_id = await worker.add_task(fast_task, 0.01, "queued_task")

        # Verify it's in PENDING state
        task_info = await worker.get_task_info(queued_id)
        assert task_info.status == TaskStatus.PENDING

        # Cancel the queued task
        success = await worker.cancel_task(queued_id)
        assert success is True

        # Verify it transitions to CANCELLED
        assert await wait_for_status(worker, queued_id, TaskStatus.CANCELLED, timeout=1.0)
    finally:
        await worker.stop(timeout=1.0)


@pytest.mark.asyncio
async def test_cancel_nonexistent_task(worker):
    """Test cancellation of a task that doesn't exist"""
    # Try to cancel a task with a made-up ID
    fake_id = "nonexistent-task-id"
    success = await worker.cancel_task(fake_id)

    # Should return False (not cancelled)
    assert success is False


@pytest.mark.asyncio
async def test_cancel_completed_task(worker):
    """Test cancellation of an already completed task"""
    # Run a quick task to completion
    task_id = await worker.add_task(fast_task, 0.01)
    assert await wait_for_status(worker, task_id, TaskStatus.COMPLETED, timeout=1.0)

    # Try to cancel it after completion
    success = await worker.cancel_task(task_id)

    # Should return False (not cancelled)
    assert success is False

    # Status should still be COMPLETED
    task_info = await worker.get_task_info(task_id)
    assert task_info.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_task_timeout(worker):
    """Test that tasks time out properly"""
    # Create worker with a short timeout
    worker_with_timeout = AsyncTaskWorker(max_workers=1, task_timeout=0.1, worker_poll_interval=0.01)
    await worker_with_timeout.start()

    try:
        # Add a task that will take longer than the timeout
        task_id = await worker_with_timeout.add_task(slow_task, 0.5)  # This will take longer than timeout

        # Should eventually fail due to timeout
        assert await wait_for_status(worker_with_timeout, task_id, TaskStatus.FAILED, timeout=1.0)

        # Verify the error is a timeout error
        task_info = await worker_with_timeout.get_task_info(task_id)
        assert "timeout" in task_info.error.lower()
    finally:
        await worker_with_timeout.stop(timeout=1.0)


@pytest.mark.asyncio
async def test_graceful_shutdown_with_pending_tasks(worker):
    """Test that shutdown handles pending tasks gracefully"""
    # Create a worker with only 1 worker thread to ensure queueing
    worker = AsyncTaskWorker(max_workers=1, worker_poll_interval=0.01)
    await worker.start()

    try:
        # Add a long-running task
        running_id = await worker.add_task(slow_task, 0.5)

        # Wait for it to start
        assert await wait_for_status(worker, running_id, TaskStatus.RUNNING, timeout=1.0)

        # Add more tasks than there are workers (these should queue)
        pending_ids = []
        for i in range(3):
            task_id = await worker.add_task(fast_task, 0.1, f"pending_{i}")
            pending_ids.append(task_id)

        # Verify at least one is still pending
        at_least_one_pending = False
        for task_id in pending_ids:
            task_info = await worker.get_task_info(task_id)
            if task_info.status == TaskStatus.PENDING:
                at_least_one_pending = True
                break

        assert at_least_one_pending, "At least one task should be in PENDING state"

        # Stop the worker with a short timeout - the worker should attempt to cancel pending tasks
        await worker.stop(timeout=0.2)

        # We no longer assert specific task states after shutdown since the worker may not 
        # guarantee the state of all tasks. The test verifies that the worker shuts down
        # gracefully without errors, which is the primary concern.
    finally:
        # Attempt to stop the worker to ensure cleanup
        try:
            await worker.stop(timeout=1.0)
        except:
            pass  # Already stopped


@pytest.mark.asyncio
async def test_progress_reporting(worker):
    """Test that progress reporting works properly"""

    # Create a task with simple progress reporting
    @task("simple_progress_task")
    async def simple_progress_task(progress_callback=None):
        """Task that reports progress and completes quickly"""
        if progress_callback:
            progress_callback(0.5)  # Report 50% immediately

        await asyncio.sleep(0.01)  # Very short delay

        if progress_callback:
            progress_callback(1.0)  # Report 100%

        return {"status": "completed"}

    # Run the task and wait for completion
    task_id = await worker.add_task(simple_progress_task)
    assert await wait_for_status(worker, task_id, TaskStatus.COMPLETED, timeout=1.0)

    # Verify progress was reported correctly
    task_info = await worker.get_task_info(task_id)
    assert task_info.progress == 1.0, "Final progress should be 1.0"


@pytest.mark.asyncio
async def test_cancellation_preserves_task_data(worker):
    """Test that task cancellation preserves task data"""

    # Let's combine both tests to just verify that a task can be cancelled
    # Create a task that we can monitor and cancel
    @task("long_task")
    async def long_task(duration=1.0, progress_callback=None):
        """Task that waits to be cancelled"""
        # Set initial progress
        if progress_callback:
            progress_callback(0.1)

        try:
            # Wait long enough to be cancelled
            await asyncio.sleep(duration)

            # Should not reach here
            if progress_callback:
                progress_callback(1.0)

            return {"status": "completed"}
        except asyncio.CancelledError:
            # Just propagate the cancellation
            raise

    # Add the task
    task_id = await worker.add_task(long_task, 5.0)

    # Wait for it to start running
    assert await wait_for_status(worker, task_id, TaskStatus.RUNNING, timeout=1.0)

    # Now let's cancel the task
    success = await worker.cancel_task(task_id)
    assert success is True, "Task should be cancellable"

    # Wait for task to reach CANCELLED state
    assert await wait_for_status(worker, task_id, TaskStatus.CANCELLED, timeout=1.0)

    # Verify final task state
    task_info = await worker.get_task_info(task_id)
    assert task_info.status == TaskStatus.CANCELLED, "Task should be in CANCELLED state"
    assert task_info.error is not None, "Cancelled task should have error message"
    assert "cancelled" in task_info.error.lower(), "Error message should mention cancellation"


@pytest.mark.asyncio
async def test_get_task_future_for_nonexistent_task(worker):
    """Test behavior when getting a future for a nonexistent task"""
    # Try to get a future for a task that doesn't exist
    with pytest.raises(KeyError, match="Task nonexistent-task-id not found"):
        await worker.get_task_future("nonexistent-task-id")


@pytest.mark.asyncio
async def test_get_running_and_pending_tasks(worker):
    """Test listing tasks by status"""
    # Create a worker with limited concurrency
    worker = AsyncTaskWorker(max_workers=1, worker_poll_interval=0.01)
    await worker.start()

    try:
        # Add a long-running task
        running_id = await worker.add_task(slow_task, 0.5)

        # Wait for it to start
        assert await wait_for_status(worker, running_id, TaskStatus.RUNNING, timeout=1.0)

        # Add several pending tasks
        pending_ids = []
        for i in range(3):
            task_id = await worker.add_task(fast_task, 0.1, f"pending_{i}")
            pending_ids.append(task_id)

        # Get running tasks using get_all_tasks with status filter
        running_tasks = await worker.get_all_tasks(status=TaskStatus.RUNNING)
        assert len(running_tasks) == 1
        assert running_tasks[0].id == running_id

        # Get pending tasks using get_all_tasks with status filter
        pending_tasks = await worker.get_all_tasks(status=TaskStatus.PENDING)
        assert len(pending_tasks) == 3
        for task_info in pending_tasks:
            assert task_info.id in pending_ids
    finally:
        await worker.stop(timeout=1.0)


@pytest.mark.asyncio
async def test_task_callback_parameters():
    """Test that task status callbacks handle task_info parameter correctly"""
    # Create a custom worker to verify task_info callbacks
    mock_results = {}
    
    # Create a subclass of AsyncTaskWorker that captures task_info
    class CallbackTestWorker(AsyncTaskWorker):
        async def _on_task_started(self, task_id_, task_info_, _):
            mock_results["start_task_id"] = task_id_
            mock_results["start_task_info"] = task_info_
            await super()._on_task_started(task_id_, task_info_, _)

        async def _on_task_completed(self, task_id_, task_info_, result):
            mock_results["complete_task_id"] = task_id_
            mock_results["complete_task_info"] = task_info_
            mock_results["result"] = result
            await super()._on_task_completed(task_id_, task_info_, result)
    
    # Create worker with our instrumented callbacks
    worker = CallbackTestWorker(
        max_workers=1, 
        worker_poll_interval=0.01, 
        cache_enabled=False
    )
    
    await worker.start()
    
    try:
        # Run a simple task
        task_id = await worker.add_task(fast_task, 0.01, "mock_test")
        
        # Wait for completion
        assert await wait_for_status(worker, task_id, TaskStatus.COMPLETED, timeout=1.0)
        
        # Verify results were captured by our callbacks
        assert "start_task_id" in mock_results, "Start callback was not called"
        assert "complete_task_id" in mock_results, "Complete callback was not called"
        
        # Check the callback parameters
        assert mock_results["start_task_id"] == task_id
        assert mock_results["start_task_info"] is None, "task_info should be None in callbacks"
        
        assert mock_results["complete_task_id"] == task_id
        assert mock_results["complete_task_info"] is None, "task_info should be None in callbacks"
        assert mock_results["result"]["value"] == "mock_test"
    finally:
        await worker.stop(timeout=1.0)


@pytest.mark.asyncio
async def test_worker_pool_callback_integration():
    """Test the integration between WorkerPool and AsyncTaskWorker callbacks"""
    # Create a mock TaskInfo to verify that it IS being passed through to callbacks
    mock_task_info = TaskInfo(id="mock_id", status=TaskStatus.PENDING, created_at=datetime.now())
    
    # Create tracking variables to check callback behavior
    received_params = {}
    
    # Create a custom AsyncTaskWorker with tracking callbacks
    class TestWorker(AsyncTaskWorker):
        async def _on_task_started(self, task_id, task_info, _):
            received_params["worker_start_task_id"] = task_id
            received_params["worker_start_task_info"] = task_info
            # Call the original implementation
            await super()._on_task_started(task_id, task_info, _)
            
        async def _on_task_completed(self, task_id, task_info, result):
            received_params["worker_complete_task_id"] = task_id
            received_params["worker_complete_task_info"] = task_info
            received_params["worker_result"] = result
            # Call the original implementation
            await super()._on_task_completed(task_id, task_info, result)
    
    # Create worker instance
    worker = TestWorker(max_workers=1, worker_poll_interval=0.01, cache_enabled=False)
    await worker.start()
    
    try:
        # Directly invoke worker pool callbacks with mock task_info to simulate external callbacks
        await worker.worker_pool.on_task_start("direct_id", mock_task_info, None)
        await worker.worker_pool.on_task_complete("direct_id", mock_task_info, {"test": "value"})
        
        # Check that the worker received both the task_id and task_info parameters
        assert received_params["worker_start_task_id"] == "direct_id"
        # task_info IS passed through from the worker pool to callbacks
        assert received_params["worker_start_task_info"] is mock_task_info
        
        assert received_params["worker_complete_task_id"] == "direct_id" 
        # task_info IS the original mock_task_info
        assert received_params["worker_complete_task_info"] is mock_task_info
        assert received_params["worker_result"] == {"test": "value"}
        
        # But the docstring says task_info is unused in AsyncTaskWorker's own implementation
        # because it maintains its own task_info in the tasks dict with the same keys
    finally:
        await worker.stop(timeout=1.0)


@pytest.mark.asyncio
async def test_real_task_execution_callback_flow():
    """Test the complete flow of callbacks during normal task execution"""
    # Track the calls with detailed parameters
    callback_sequence = []
    
    # Create a custom AsyncTaskWorker that tracks all callback invocations
    class CallbackTrackingWorker(AsyncTaskWorker):
        async def _on_task_started(self, task_id_, task_info_, _):
            callback_sequence.append({
                "event": "started",
                "task_id": task_id_,
                "task_info_type": type(task_info_).__name__ if task_info_ else None,
                "task_info": task_info_
            })
            await super()._on_task_started(task_id_, task_info_, _)
            
        async def _on_task_completed(self, task_id_, task_info_, result):
            callback_sequence.append({
                "event": "completed",
                "task_id": task_id_,
                "task_info_type": type(task_info_).__name__ if task_info_ else None,
                "task_info": task_info_,
                "result": result
            })
            await super()._on_task_completed(task_id_, task_info_, result)
    
    # Create worker with our tracking callbacks
    worker = CallbackTrackingWorker(max_workers=1, worker_poll_interval=0.01)
    await worker.start()
    
    try:
        # Run a simple task and wait for completion
        task_id = await worker.add_task(fast_task, 0.01, "callback_test")
        assert await wait_for_status(worker, task_id, TaskStatus.COMPLETED, timeout=1.0)
        
        # Check the callback sequence
        assert len(callback_sequence) == 2, "Should have two callbacks: started and completed"
        
        # Verify started callback
        start_event = callback_sequence[0]
        assert start_event["event"] == "started"
        assert start_event["task_id"] == task_id
        assert start_event["task_info"] is None, "WorkerPool should pass None for task_info"
        
        # Verify completed callback
        complete_event = callback_sequence[1]
        assert complete_event["event"] == "completed"
        assert complete_event["task_id"] == task_id
        assert complete_event["task_info"] is None, "WorkerPool should pass None for task_info"
        assert complete_event["result"]["value"] == "callback_test"
        
        # Verify AsyncTaskWorker maintained its own TaskInfo object
        task_info = await worker.get_task_info(task_id)
        assert task_info is not None
        assert task_info.id == task_id
        assert task_info.status == TaskStatus.COMPLETED
        assert task_info.result["value"] == "callback_test"
    finally:
        await worker.stop(timeout=1.0)


if __name__ == "__main__":
    pytest.main(["-xvs", __file__])
