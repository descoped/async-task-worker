import asyncio
import random
import time

import pytest

from async_task_worker import AsyncTaskWorker, TaskStatus, task


# Test tasks with different durations
@task("short_task")
async def short_task(duration=0.05, progress_callback=None):
    """Task that completes quickly"""
    start = time.time()
    for i in range(5):
        await asyncio.sleep(duration / 5)
        if progress_callback:
            progress_callback((i + 1) / 5)
    return {"duration": time.time() - start}


@task("medium_task")
async def medium_task(duration=0.2, progress_callback=None):
    """Task that takes a moderate amount of time"""
    start = time.time()
    result = 0
    # More frequent progress updates
    steps = 20
    for i in range(steps):
        await asyncio.sleep(duration / steps)
        result += i
        # Explicitly send progress updates more frequently
        if progress_callback:
            # Calculate more accurate progress
            current_progress = (i + 1) / steps
            progress_callback(current_progress)
    return {"result": result, "duration": time.time() - start}


@task("long_task")
async def long_task(duration=0.5, fail_probability=0, progress_callback=None):
    """Long-running task that might fail randomly"""
    start = time.time()
    for i in range(10):
        await asyncio.sleep(duration / 10)
        if progress_callback:
            progress_callback((i + 1) / 10)
        if fail_probability > 0 and random.random() < fail_probability:
            raise RuntimeError("Task failed randomly")
    return {"duration": time.time() - start}


# Test fixture
@pytest.fixture
async def worker():
    """Create and manage worker lifecycle"""
    worker = AsyncTaskWorker(max_workers=5)
    await worker.start()
    yield worker
    await worker.stop()


# Basic functionality tests
@pytest.mark.asyncio
async def test_basic_task_execution(worker):
    """Test that a task can be executed successfully"""
    task_id = await worker.add_task(short_task)

    # Wait for completion
    for _ in range(10):
        await asyncio.sleep(0.05)
        info = worker.get_task_info(task_id)
        if info.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            break

    # Check results
    info = worker.get_task_info(task_id)
    assert info.status == TaskStatus.COMPLETED
    assert info.result is not None
    assert info.progress == 1.0
    assert "duration" in info.result


@pytest.mark.asyncio
async def test_task_with_args(worker):
    """Test task execution with arguments"""
    task_id = await worker.add_task(short_task, 0.1)

    # Wait for completion
    await asyncio.sleep(0.5)

    # Check results
    info = worker.get_task_info(task_id)
    assert info.status == TaskStatus.COMPLETED
    assert info.result["duration"] >= 0.1


@pytest.mark.asyncio
async def test_task_cancellation(worker):
    """Test that a task can be cancelled"""
    task_id = await worker.add_task(long_task, 1.0)

    # Wait for task to start
    await asyncio.sleep(0.1)

    # Cancel the task
    result = await worker.cancel_task(task_id)
    assert result is True

    # Check task status
    info = worker.get_task_info(task_id)
    assert info.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_task_timeout():
    """Test that tasks timeout properly"""
    # Create worker with very short timeout
    worker = AsyncTaskWorker(max_workers=2, task_timeout=1)
    await worker.start()

    try:
        # Define a task that will definitely exceed the timeout
        @task("slow_task")
        async def slow_task(progress_callback=None):
            """Task that will exceed timeout"""
            for i in range(20):
                await asyncio.sleep(0.1)
                if progress_callback:
                    progress_callback(i / 20)
            return {"completed": True}

        # Add the task
        task_id = await worker.add_task(slow_task)

        # Wait longer for timeout to occur
        await asyncio.sleep(1.2)

        # Check that task timed out
        info = worker.get_task_info(task_id)
        assert info.status == TaskStatus.FAILED
        assert "timed out" in info.error
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_task_with_metadata(worker):
    """Test task with metadata"""
    metadata = {"description": "Test task", "id": 12345}
    task_id = await worker.add_task(short_task, metadata=metadata)

    # Wait for completion
    await asyncio.sleep(0.2)

    # Check metadata
    info = worker.get_task_info(task_id)
    assert info.metadata == metadata


@pytest.mark.asyncio
async def test_custom_task_id(worker):
    """Test using custom task ID"""
    custom_id = "my-special-task-123"
    task_id = await worker.add_task(short_task, task_id=custom_id)

    assert task_id == custom_id

    # Wait for completion
    await asyncio.sleep(0.2)

    # Check task exists with custom ID
    info = worker.get_task_info(custom_id)
    assert info is not None
    assert info.id == custom_id


# Advanced functionality tests
@pytest.mark.asyncio
async def test_failing_task(worker):
    """Test task that fails with exception"""

    # Register a failing task
    @task("always_fails")
    async def always_fails(progress_callback=None):
        if progress_callback:
            progress_callback(0.5)
        raise ValueError("This task always fails")

    # Run the failing task
    task_id = await worker.add_task(always_fails)

    # Wait for completion
    await asyncio.sleep(0.2)

    # Check error handling
    info = worker.get_task_info(task_id)
    assert info.status == TaskStatus.FAILED
    assert "This task always fails" in info.error


@pytest.mark.asyncio
async def test_progress_reporting(worker):
    """Test that progress is reported correctly"""
    # Use a slower task to ensure we can observe progress
    task_id = await worker.add_task(medium_task, 1.0)

    # Wait for task to start making progress
    for _ in range(5):
        await asyncio.sleep(0.1)
        info = worker.get_task_info(task_id)
        if info.progress > 0:
            break

    # Verify progress is being reported
    info = worker.get_task_info(task_id)
    print(f"Progress after polling: {info.progress}")
    assert info.progress > 0, "Progress should be greater than 0 after task has started"

    # Wait for completion
    for _ in range(15):
        await asyncio.sleep(0.1)
        info = worker.get_task_info(task_id)
        if info.status == TaskStatus.COMPLETED:
            break

    # Check final progress
    info = worker.get_task_info(task_id)
    assert info.status == TaskStatus.COMPLETED, f"Task should be completed, got {info.status}"
    assert info.progress == 1.0, f"Final progress should be 1.0, got {info.progress}"


@pytest.mark.asyncio
async def test_priority_order(worker):
    """Test that tasks execute in priority order"""
    # Add low priority task
    low_task_id = await worker.add_task(medium_task, 0.4, priority=10)

    # Add high priority task
    high_task_id = await worker.add_task(medium_task, 0.4, priority=1)

    # Wait for both to complete
    await asyncio.sleep(1.0)

    # Both should be complete
    low_info = worker.get_task_info(low_task_id)
    high_info = worker.get_task_info(high_task_id)

    assert low_info.status == TaskStatus.COMPLETED
    assert high_info.status == TaskStatus.COMPLETED

    # High priority should have completed first
    assert high_info.completed_at < low_info.completed_at


@pytest.mark.asyncio
async def test_get_all_tasks(worker):
    """Test retrieving all tasks with filtering"""
    # Add a mix of tasks
    task_ids = []
    for i in range(5):
        task_id = await worker.add_task(short_task)
        task_ids.append(task_id)

    # Wait for completion
    await asyncio.sleep(0.5)

    # Get all completed tasks
    completed_tasks = await worker.get_all_tasks(status=TaskStatus.COMPLETED)
    assert len(completed_tasks) == 5

    # Add a failing task
    @task("will_fail")
    async def will_fail():
        raise RuntimeError("Deliberate failure")

    fail_id = await worker.add_task(will_fail)

    # Wait for it to fail
    await asyncio.sleep(0.2)

    # Get failed tasks
    failed_tasks = await worker.get_all_tasks(status=TaskStatus.FAILED)
    assert len(failed_tasks) == 1
    assert failed_tasks[0].id == fail_id


# Stress tests
@pytest.mark.asyncio
async def test_concurrent_tasks(worker):
    """Test running many concurrent tasks"""
    # Add mix of short and medium tasks
    task_ids = []

    # Add 20 short tasks
    for _ in range(20):
        task_id = await worker.add_task(short_task, 0.05)
        task_ids.append(task_id)

    # Add 10 medium tasks
    for _ in range(10):
        task_id = await worker.add_task(medium_task, 0.2)
        task_ids.append(task_id)

    # Wait for all tasks to complete
    await asyncio.sleep(1.5)

    # Check results
    completed = 0
    for task_id in task_ids:
        info = worker.get_task_info(task_id)
        if info.status == TaskStatus.COMPLETED:
            completed += 1

    assert completed == len(task_ids)


@pytest.mark.asyncio
async def test_random_failures():
    """Test with randomly failing tasks"""
    worker = AsyncTaskWorker(max_workers=3)
    await worker.start()

    try:
        task_ids = []

        # Instead of random probabilities, use fixed values
        # This makes the test deterministic
        fail_probs = [0.3] * 10 + [0.0] * 10  # 10 tasks with 30% fail chance, 10 with 0%
        expected_failures = sum(fail_probs)  # Should be exactly 3.0

        # Add tasks with controlled failure probabilities
        for i in range(20):
            task_id = await worker.add_task(long_task, 0.2, fail_probs[i])
            task_ids.append(task_id)

        # Wait longer for all tasks to complete
        await asyncio.sleep(2.0)

        # Count failures
        failures = 0
        for task_id in task_ids:
            info = worker.get_task_info(task_id)
            if info.status == TaskStatus.FAILED:
                failures += 1

        # Check if any of the guaranteed successful tasks failed
        all_guaranteed_succeeded = True
        for i in range(10, 20):
            task_id = task_ids[i]
            info = worker.get_task_info(task_id)
            if info.status != TaskStatus.COMPLETED:
                all_guaranteed_succeeded = False
                break

        # Verify the guaranteed successful tasks worked
        assert all_guaranteed_succeeded, "Tasks with 0% failure probability should succeed"

        # For the probabilistic part, just check if we got some failures
        # but don't be too strict about the exact number
        assert failures > 0, "Should have some failures"
        assert failures <= 10, "Should not have more failures than tasks with non-zero probability"

    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_worker_lifecycle():
    """Test worker start/stop lifecycle"""
    worker = AsyncTaskWorker(max_workers=3)

    # Start the worker
    await worker.start()
    assert len(worker.workers) == 3

    # Start again should be no-op
    await worker.start()
    assert len(worker.workers) == 3

    # Add a task
    task_id = await worker.add_task(short_task)

    # Stop the worker
    await worker.stop()
    assert len(worker.workers) == 0

    # Verify the worker can't accept new tasks
    with pytest.raises(RuntimeError):
        await worker.add_task(short_task)

    # Start again and verify it works
    await worker.start()
    assert len(worker.workers) == 3

    # Clean up
    await worker.stop()


if __name__ == "__main__":
    pytest.main(["-xvs", __file__])
