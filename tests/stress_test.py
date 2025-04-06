#!/usr/bin/env python
"""
AsyncTaskWorker Stress Test Script

Run various stress test scenarios on the AsyncTaskWorker.

Usage:
    python stress_test.py [scenario]

Available scenarios:
    basic, concurrent, priority, longrun, failures, all (default)
"""

import asyncio
import random
import sys
import time

from async_task_worker import AsyncTaskWorker, TaskStatus, task


# Define test tasks with different durations
@task("short_task")
async def short_task(duration=0.05, progress_callback=None):
    """Task that completes quickly"""
    start = time.time()
    for i in range(10):
        await asyncio.sleep(duration / 10)
        if progress_callback:
            progress_callback((i + 1) / 10)
    return {"duration": time.time() - start}


@task("medium_task")
async def medium_task(duration=0.5, iterations=10, progress_callback=None):
    """Task that takes a moderate amount of time"""
    start = time.time()
    results = []
    for i in range(iterations):
        await asyncio.sleep(duration / iterations)
        results.append(i * i)
        if progress_callback:
            progress_callback((i + 1) / iterations)
    return {"results": results, "duration": time.time() - start}


@task("long_task")
async def long_task(duration=2.0, fail_probability=0, progress_callback=None):
    """Long-running task that might fail randomly"""
    start = time.time()
    steps = 20
    for i in range(steps):
        await asyncio.sleep(duration / steps)
        if progress_callback:
            progress_callback((i + 1) / steps)
        if fail_probability > 0 and random.random() < fail_probability:
            raise RuntimeError("Task failed randomly")
    return {"duration": time.time() - start}


@task("cpu_task")
async def cpu_task(iterations=10000, progress_callback=None):
    """CPU-intensive task"""
    result = 0
    for i in range(iterations):
        # CPU-bound work
        result += i ** 2
        if i % (iterations // 10) == 0:
            if progress_callback:
                progress_callback(i / iterations)
            # Allow other tasks to run
            await asyncio.sleep(0)
    return {"result": result}


# Test scenarios
async def test_basic_functionality(workers=3, task_count=10):
    """Basic functionality test"""
    print(f"\n--- Basic Functionality Test (workers={workers}, tasks={task_count}) ---")

    worker = AsyncTaskWorker(max_workers=workers, task_timeout=10)
    await worker.start()

    try:
        start_time = time.time()
        task_ids = []

        # Add a mix of task types
        for _ in range(task_count):
            task_type = random.choice(["short_task", "medium_task"])
            task_func = globals()[task_type]
            task_id = await worker.add_task(task_func)
            task_ids.append(task_id)

        # Wait for completion
        await monitor_tasks(worker, task_ids)

        # Print results
        elapsed = time.time() - start_time
        print(f"Completed {task_count} tasks in {elapsed:.2f} seconds")
        print_task_stats(worker, task_ids)

    finally:
        await worker.stop()


async def test_concurrent_tasks(workers=10, task_count=100):
    """Test with high concurrency"""
    print(f"\n--- Concurrent Tasks Test (workers={workers}, tasks={task_count}) ---")

    worker = AsyncTaskWorker(max_workers=workers, task_timeout=30)
    await worker.start()

    try:
        # Add many short tasks
        print("Adding many concurrent tasks...")
        start_time = time.time()
        task_ids = []

        for _ in range(task_count):
            task_id = await worker.add_task(short_task, 0.1)
            task_ids.append(task_id)

        # Wait for completion
        await monitor_tasks(worker, task_ids)

        # Print results
        elapsed = time.time() - start_time
        tasks_per_second = task_count / elapsed
        print(f"Completed {task_count} tasks in {elapsed:.2f} seconds")
        print(f"Throughput: {tasks_per_second:.2f} tasks/second")

    finally:
        await worker.stop()


async def test_priority_handling(workers=5, task_count=30):
    """Test priority handling under load"""
    print(f"\n--- Priority Handling Test (workers={workers}, tasks={task_count}) ---")

    worker = AsyncTaskWorker(max_workers=workers, task_timeout=20)
    await worker.start()

    try:
        start_time = time.time()
        task_data = {}  # Store task info by priority

        # Custom task to track completion times
        @task("priority_task")
        async def priority_task(name_, duration=0.2, progress_callback=None):
            await asyncio.sleep(duration)
            if progress_callback:
                progress_callback(1.0)
            return {"name": name_, "time": time.time() - start_time}

        # Add tasks with different priorities in mixed order
        priorities = {
            "high": 1,
            "medium": 5,
            "low": 10
        }

        for priority_name, priority_value in priorities.items():
            task_data[priority_name] = {"ids": [], "times": []}
            for i in range(task_count // 3):
                name = f"{priority_name}_{i}"
                task_id = await worker.add_task(
                    priority_task,
                    name,
                    0.2,
                    priority=priority_value
                )
                task_data[priority_name]["ids"].append(task_id)

        # Collect all task IDs
        all_task_ids = []
        for data in task_data.values():
            all_task_ids.extend(data["ids"])

        # Wait for completion
        await monitor_tasks(worker, all_task_ids)

        # Analyze completion times by priority
        for priority_name, data in task_data.items():
            for task_id in data["ids"]:
                info = worker.get_task_info(task_id)
                if info.status == TaskStatus.COMPLETED:
                    data["times"].append(info.result["time"])

        # Print results
        print("\nAverage completion times by priority:")
        for priority_name, data in task_data.items():
            if data["times"]:
                avg_time = sum(data["times"]) / len(data["times"])
                print(f"{priority_name.capitalize()}: {avg_time:.3f}s")

        # Check if priorities were respected
        if (task_data["high"]["times"] and task_data["low"]["times"] and
                min(task_data["high"]["times"]) < min(task_data["low"]["times"])):
            print("✓ Priority system working correctly")
        else:
            print("❌ Priority issues detected")

    finally:
        await worker.stop()


async def test_long_running_tasks(workers=3, task_count=6):
    """Test with long-running tasks"""
    print(f"\n--- Long-Running Tasks Test (workers={workers}, tasks={task_count}) ---")

    worker = AsyncTaskWorker(max_workers=workers, task_timeout=15)
    await worker.start()

    try:
        start_time = time.time()
        task_ids = []

        # Add long-running tasks
        for i in range(task_count):
            duration = random.uniform(2, 5)
            task_id = await worker.add_task(
                long_task,
                duration,
                metadata={"expected_duration": duration}
            )
            task_ids.append(task_id)

        # Wait and monitor progress
        await monitor_tasks(worker, task_ids, print_progress=True)

        # Print results
        elapsed = time.time() - start_time
        print(f"Completed {task_count} long-running tasks in {elapsed:.2f} seconds")

    finally:
        await worker.stop()


async def test_failing_tasks(workers=5, task_count=20):
    """Test with failing tasks"""
    print(f"\n--- Failing Tasks Test (workers={workers}, tasks={task_count}) ---")

    worker = AsyncTaskWorker(max_workers=workers, task_timeout=10)
    await worker.start()

    try:
        start_time = time.time()
        task_ids = []
        expected_failures = 0

        # Add tasks with different failure rates
        for _ in range(task_count):
            fail_prob = random.uniform(0, 0.7)
            expected_failures += fail_prob
            task_id = await worker.add_task(
                long_task,
                0.5,
                fail_prob,
                metadata={"failure_probability": fail_prob}
            )
            task_ids.append(task_id)

        # Wait for completion
        await monitor_tasks(worker, task_ids)

        # Print results
        elapsed = time.time() - start_time
        print(f"Processed {task_count} tasks in {elapsed:.2f} seconds")

        # Count failures
        statuses = {}
        for task_id in task_ids:
            info = worker.get_task_info(task_id)
            if info.status not in statuses:
                statuses[info.status] = 0
            statuses[info.status] += 1

        print("\nTask status distribution:")
        for status, count in statuses.items():
            print(f"{status}: {count} ({count / task_count * 100:.1f}%)")

        failure_count = statuses.get(TaskStatus.FAILED, 0)
        print(f"Expected ~{expected_failures:.1f} failures, got {failure_count}")

    finally:
        await worker.stop()


# Helper functions
async def monitor_tasks(worker, task_ids, timeout=30, interval=0.5, print_progress=False):
    """Monitor tasks until completion or timeout"""
    start_time = time.time()
    last_progress_time = 0

    while time.time() - start_time < timeout:
        # Check status of all tasks
        statuses = {}
        total_progress = 0

        for task_id in task_ids:
            info = worker.get_task_info(task_id)
            if info.status not in statuses:
                statuses[info.status] = 0
            statuses[info.status] += 1
            total_progress += info.progress

        # Calculate completion percentage
        pending_or_running = statuses.get(TaskStatus.PENDING, 0) + statuses.get(TaskStatus.RUNNING, 0)

        # If all tasks completed or failed, we're done
        if pending_or_running == 0:
            break

        # Print progress periodically
        current_time = time.time()
        if print_progress and current_time - last_progress_time >= 1.0:
            avg_progress = total_progress / len(task_ids) * 100
            elapsed = current_time - start_time
            print(f"Progress: {avg_progress:.1f}% after {elapsed:.1f}s | "
                  f"Running: {statuses.get(TaskStatus.RUNNING, 0)}, "
                  f"Pending: {statuses.get(TaskStatus.PENDING, 0)}")
            last_progress_time = current_time

        # Short sleep before checking again
        await asyncio.sleep(interval)

    # Return whether we timed out
    return time.time() - start_time < timeout


def print_task_stats(worker, task_ids):
    """Print statistics about task execution"""
    statuses = {}
    durations = []

    for task_id in task_ids:
        info = worker.get_task_info(task_id)

        # Count statuses
        if info.status not in statuses:
            statuses[info.status] = 0
        statuses[info.status] += 1

        # Collect durations for completed tasks
        if info.status == TaskStatus.COMPLETED:
            if info.started_at and info.completed_at:
                duration = (info.completed_at - info.started_at).total_seconds()
                durations.append(duration)

    # Print status counts
    print("\nTask Status Statistics:")
    for status, count in statuses.items():
        print(f"{status}: {count} tasks ({count / len(task_ids) * 100:.1f}%)")

    # Print duration statistics if we have completed tasks
    if durations:
        avg_duration = sum(durations) / len(durations)
        min_duration = min(durations)
        max_duration = max(durations)
        print(f"\nTask Duration Statistics:")
        print(f"Average: {avg_duration:.3f}s | Min: {min_duration:.3f}s | Max: {max_duration:.3f}s")


async def run_all_tests():
    """Run all test scenarios"""
    await test_basic_functionality()
    await test_concurrent_tasks()
    await test_priority_handling()
    await test_long_running_tasks()
    await test_failing_tasks()


async def main():
    """Main entry point"""
    # Get test scenario from command line or default to all
    scenario = sys.argv[1] if len(sys.argv) > 1 else "all"

    # Map of available test scenarios to their corresponding functions
    scenarios = {
        "basic": test_basic_functionality,
        "concurrent": test_concurrent_tasks,
        "priority": test_priority_handling,
        "longrun": test_long_running_tasks,
        "failures": test_failing_tasks,
        "all": run_all_tests
    }

    # Run the selected scenario if it exists
    if scenario in scenarios:
        await scenarios[scenario]()
    else:
        print(f"Unknown scenario: {scenario}")
        print(f"Available scenarios: {', '.join(scenarios.keys())}")


if __name__ == "__main__":
    asyncio.run(main())
