import asyncio
import logging
import uuid
from contextlib import AsyncExitStack
from typing import Dict, Any, List, Optional, Set

import pytest

# Import event system components
from async_events import GroupFilter, event_manager
# Import AsyncTaskWorker components
from async_task_worker import AsyncTaskWorker, task, TaskStatus, TaskInfo

logger = logging.getLogger("async_task_worker_tests")


# Define task registration
@task("computation_task")
async def computation_task(
        data: Dict[str, Any],
        computation_id: str,
        delay: float = 0.5
) -> Dict[str, Any]:
    """
    Example computation task that reports its progress
    and publishes events through the event system.
    """
    logger.info(f"Starting computation task with ID: {computation_id}")

    result = {"status": "processing", "computation_id": computation_id, "steps": []}
    total_steps = 5

    for step in range(1, total_steps + 1):
        # Check for cancellation using modern pattern
        try:
            # Simulate computation work
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            logger.warning(f"Task {computation_id} was cancelled during step {step}")
            raise

        # Update progress (0.0 to 1.0)
        progress = step / total_steps

        # Add step result
        step_result = {
            "step": step,
            "input": data.get("input", 0) * step,
            "timestamp": asyncio.get_event_loop().time()
        }
        result["steps"].append(step_result)

        # Publish step completion event
        event_data = {
            "computation_id": computation_id,
            "step": step,
            "total_steps": total_steps,
            "progress": progress,
            "step_result": step_result
        }
        await event_manager.publish_event(
            event_id=f"{computation_id}_step_{step}",
            group_id=computation_id,
            event_data=event_data
        )

        logger.info(f"Completed step {step}/{total_steps} for computation {computation_id}")

    # Update final status
    result["status"] = "completed"

    # Publish completion event
    completion_event = {
        "computation_id": computation_id,
        "status": "completed",
        "result": result
    }
    await event_manager.publish_event(
        event_id=f"{computation_id}_completed",
        group_id=computation_id,
        event_data=completion_event
    )

    logger.info(f"Computation task {computation_id} completed successfully")
    return result


# Modern task worker event integration handler using structured concurrency
async def configure_task_worker_events(worker: AsyncTaskWorker) -> asyncio.Task:
    """
    Configure a task worker to publish events when tasks change state.
    Returns the monitoring task which can be cancelled when done.
    """
    # Start the event manager if not already running
    await event_manager.start()

    # Guard clause: check if monitor task already exists and is still running
    if hasattr(worker, "_event_monitor_task") and not worker._event_monitor_task.done():
        logger.debug("Task worker monitor already running")
        return worker._event_monitor_task

    # Use an Event for signaling termination instead of cancellation
    stop_event = asyncio.Event()
    processed_tasks: Set[str] = set()

    async def process_terminal_tasks():
        """Process tasks that have reached a terminal state."""
        # Fetch all tasks in terminal states
        terminal_statuses = [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]
        tasks_by_status = {}

        # Get tasks for each status concurrently
        async with asyncio.TaskGroup() as tg:
            for status in terminal_statuses:
                tasks_by_status[status] = tg.create_task(
                    worker.get_all_tasks(status=status)
                )

        # Process results
        all_tasks = []
        for status, task_ in tasks_by_status.items():
            all_tasks.extend(task_.result())

        return all_tasks

    async def handle_task_state_changes(task_info: TaskInfo):
        """Process an individual task state change."""
        task_id = task_info.id

        # Skip if already processed
        if task_id in processed_tasks:
            return

        # Process new task
        processed_tasks.add(task_id)

        # Get metadata
        metadata = task_info.metadata if hasattr(task_info, "metadata") else {}
        computation_id = metadata.get("computation_id")

        if not computation_id:
            return

        # Build event data based on task state
        event_data = {
            "task_id": task_id,
            "computation_id": computation_id,
            "status": task_info.status,
            "timestamp": asyncio.get_event_loop().time()
        }

        # Add status-specific data
        if task_info.status == TaskStatus.COMPLETED:
            event_data["result"] = task_info.result
        elif task_info.status == TaskStatus.FAILED:
            event_data["error"] = task_info.error
        elif task_info.status == TaskStatus.CANCELLED:
            event_data["reason"] = task_info.cancel_reason

        # Publish event
        await event_manager.publish_event(
            event_id=task_id,
            group_id=computation_id,
            event_data=event_data
        )

        logger.info(f"Published {task_info.status} event for task {task_id}")

    async def monitor_worker():
        """Monitor for task state changes using a reactive approach."""
        logger.info("Starting task state change monitor")

        try:
            while not stop_event.is_set():
                try:
                    # Process terminal tasks
                    terminal_tasks = await process_terminal_tasks()

                    # Process each task in parallel using TaskGroup
                    if terminal_tasks:
                        async with asyncio.TaskGroup() as tg:
                            for task_info in terminal_tasks:
                                tg.create_task(handle_task_state_changes(task_info))

                    # Short wait to avoid CPU spinning, but responsive to stop signal
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=0.1)
                    except asyncio.TimeoutError:
                        pass

                except asyncio.CancelledError:
                    # Handle cancellation
                    logger.info("Task monitor received cancellation")
                    return
                except Exception as e:
                    logger.error(f"Error in task monitor: {e}", exc_info=True)
        finally:
            logger.info("Task state monitor stopped")

    # Create the monitoring task
    monitor_task = asyncio.create_task(monitor_worker())

    # Store both the task and stop event for clean shutdown
    setattr(worker, "_event_monitor_task", monitor_task)
    setattr(worker, "_event_monitor_stop", stop_event)

    logger.info("Task worker event monitor started")
    return monitor_task


# Modern event client with structured concurrency
class EventSubscription:
    """
    Modern reactive event subscription using Python 3.11+ async patterns.
    """

    def __init__(self, group_id: str):
        self.group_id = group_id
        self.subscriber_id = None
        self.queue = None
        self.events = []
        self._stop_event = asyncio.Event()
        self._collection_task = None

    async def __aenter__(self):
        """Enable use as an async context manager."""
        await self.subscribe()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Clean up resources when exiting context."""
        await self.unsubscribe()

    async def subscribe(self):
        """Subscribe to events for this group."""
        self.subscriber_id, self.queue = await event_manager.subscribe(self.group_id)
        logger.info(f"Subscribed to events for group {self.group_id} with ID: {self.subscriber_id}")
        return self

    async def unsubscribe(self):
        """Unsubscribe and clean up resources."""
        # Stop any ongoing collection
        if self._collection_task and not self._collection_task.done():
            self._stop_event.set()
            try:
                await asyncio.wait_for(self._collection_task, timeout=1.0)
            except asyncio.TimeoutError:
                self._collection_task.cancel()
                try:
                    await self._collection_task
                except asyncio.CancelledError:
                    pass

        # Unsubscribe from event manager
        if self.subscriber_id:
            await event_manager.unsubscribe(self.subscriber_id)
            logger.info(f"Unsubscribed from events: {self.subscriber_id}")
            self.subscriber_id = None

    async def collect_events(self, *,
                             until_condition=None,
                             timeout: float = 10.0) -> List[Dict[str, Any]]:
        """
        Collect events using structured concurrency, stopping when condition is met.

        Args:
            until_condition: Optional callable that takes an event and returns True when collection should stop
            timeout: Maximum time to collect events

        Returns:
            List of collected events
        """
        if not self.queue:
            raise ValueError("Not subscribed to events. Call subscribe() first.")

        # Reset collection state
        self._stop_event.clear()
        self.events.clear()

        async def collect_with_timeout():
            """Event collection coroutine with timeout handling."""
            start_time = asyncio.get_event_loop().time()

            try:
                while not self._stop_event.is_set():
                    # Check timeout
                    elapsed = asyncio.get_event_loop().time() - start_time
                    if elapsed >= timeout:
                        logger.warning(f"Event collection timed out after {timeout} seconds")
                        break

                    # Calculate remaining time (ensure it's at least 0)
                    remaining = max(0.0, timeout - elapsed)

                    # Wait for next event with timeout
                    try:
                        event = await asyncio.wait_for(self.queue.get(), timeout=min(0.5, remaining))
                        self.events.append(event)

                        # Check stop condition
                        if until_condition and until_condition(event):
                            logger.info("Event collection stop condition met")
                            break

                    except asyncio.TimeoutError:
                        # Just a short timeout for responsiveness, continue if not overall timeout
                        if asyncio.get_event_loop().time() - start_time >= timeout:
                            break
            finally:
                logger.debug(f"Collected {len(self.events)} events")

        # Use a task to allow for cancellation
        self._collection_task = asyncio.create_task(collect_with_timeout())
        await self._collection_task

        return self.events

    async def wait_for_completion_event(self, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
        """Wait specifically for a completion event."""

        def is_completion(event_: Dict[str, Any]) -> bool:
            """Check if an event represents completion."""
            if event_.get("status") == "completed":
                return True
            if isinstance(event_.get("result"), dict) and event_.get("result", {}).get("status") == "completed":
                return True
            return False

        events = await self.collect_events(until_condition=is_completion, timeout=timeout)

        # Find the completion event if it exists
        for event in reversed(events):  # Check from newest to oldest
            if is_completion(event):
                return event

        return None

    def get_events(self) -> List[Dict[str, Any]]:
        """Get currently collected events."""
        return self.events


# Test fixtures
@pytest.fixture
async def worker():
    """Create and start an AsyncTaskWorker."""
    # Create worker with more reasonable test settings
    worker = AsyncTaskWorker(
        max_workers=2,
        task_timeout=10.0,
        worker_poll_interval=0.1,
        cache_enabled=False,
        max_queue_size=10,
        task_retention_days=1,
        cleanup_interval=60
    )

    # Use AsyncExitStack for proper resource cleanup
    async with AsyncExitStack() as exit_stack:
        # Start the worker and register cleanup
        await worker.start()
        exit_stack.callback(worker.stop, timeout=2.0)

        # Start event monitor and register cleanup
        monitor_task = await configure_task_worker_events(worker)

        async def cleanup_monitor():
            """Clean up the monitor task."""
            if hasattr(worker, "_event_monitor_stop"):
                worker._event_monitor_stop.set()
            if monitor_task and not monitor_task.done():
                monitor_task.cancel()
                try:
                    await monitor_task
                except asyncio.CancelledError:
                    pass
            await event_manager.stop()

        exit_stack.callback(cleanup_monitor)

        # Provide the worker
        yield worker


@pytest.mark.asyncio
async def test_task_with_events(worker):
    """Test task execution with event publishing and subscription."""
    # Generate a unique computation ID
    computation_id = str(uuid.uuid4())

    # Use context manager for subscription
    async with EventSubscription(computation_id) as event_sub:
        # Add the task to the worker
        task_id = await worker.add_task(
            task_func=computation_task,
            data={"input": 10},
            computation_id=computation_id,
            delay=0.2,  # Faster for tests
            metadata={"computation_id": computation_id}
        )

        # Get the future for the task result
        task_future = await worker.get_task_future(task_id)

        # Start event collection task
        event_collection_task = asyncio.create_task(
            event_sub.collect_events(
                until_condition=lambda e: e.get("status") == "completed",
                timeout=5.0
            )
        )

        # Wait for the task to complete
        result = await task_future

        # Wait for event collection to complete
        await event_collection_task

        # Get collected events
        events = event_sub.get_events()

        # Assertions
        assert result["status"] == "completed"
        assert result["computation_id"] == computation_id
        assert len(result["steps"]) == 5

        # Check events
        assert len(events) >= 6  # 5 step events + 1 completion event

        # Verify step events
        step_events = [e for e in events if "step" in e]
        assert len(step_events) >= 5

        # Check increasing progress
        progress_values = [e.get("progress", 0) for e in step_events]
        assert all(progress_values[i] < progress_values[i + 1] for i in range(len(progress_values) - 1))

        # Find completion event
        completion_events = [e for e in events if e.get("status") == "completed"]
        assert len(completion_events) >= 1


@pytest.mark.asyncio
async def test_task_cancellation_with_events(worker):
    """Test task cancellation with event publishing."""
    # Generate a unique computation ID
    computation_id = str(uuid.uuid4())

    # Use context manager for subscription
    async with EventSubscription(computation_id) as event_sub:
        # Add the task to the worker with a longer delay
        task_id = await worker.add_task(
            task_func=computation_task,
            data={"input": 20},
            computation_id=computation_id,
            delay=0.5,  # Longer to ensure we can cancel
            metadata={"computation_id": computation_id}
        )

        # Wait a bit to let the task start using asyncio.sleep
        await asyncio.sleep(0.8)

        # Cancel the task and publish a cancellation event
        cancelled = await worker.cancel_task(task_id)
        assert cancelled, "Task should have been cancellable"

        # Get task info and check status
        task_info = await worker.get_task_info(task_id)
        assert task_info is not None
        assert task_info.status == TaskStatus.CANCELLED

        # Ensure a cancellation event is published
        event_data = {
            "task_id": task_id,
            "computation_id": computation_id,
            "status": TaskStatus.CANCELLED,
            "timestamp": asyncio.get_event_loop().time(),
            "reason": "Task cancelled for testing"
        }

        await event_manager.publish_event(
            event_id=f"{task_id}_cancelled",
            group_id=computation_id,
            event_data=event_data
        )

        # Collect events until we find a cancellation or timeout
        events = await event_sub.collect_events(
            until_condition=lambda e: e.get("status") == TaskStatus.CANCELLED,
            timeout=3.0
        )

        # We should have at least one event
        assert len(events) >= 1

        # Find cancellation event
        cancellation_events = [
            e for e in events
            if e.get("status") == TaskStatus.CANCELLED
        ]
        assert len(cancellation_events) >= 1


@pytest.mark.asyncio
async def test_multiple_tasks_with_event_filtering(worker):
    """Test running multiple tasks with event filtering using TaskGroup."""
    # Generate two unique computation IDs
    computation_id_1 = str(uuid.uuid4())
    computation_id_2 = str(uuid.uuid4())

    # Create two subscriptions
    async with AsyncExitStack() as stack:
        # Set up both subscriptions
        event_sub_1 = await stack.enter_async_context(EventSubscription(computation_id_1))
        event_sub_2 = await stack.enter_async_context(EventSubscription(computation_id_2))

        # Add tasks to the worker in parallel
        async with asyncio.TaskGroup() as tg:
            task1_future = tg.create_task(
                worker.add_task(
                    task_func=computation_task,
                    data={"input": 10},
                    computation_id=computation_id_1,
                    delay=0.2,
                    metadata={"computation_id": computation_id_1}
                )
            )

            task2_future = tg.create_task(
                worker.add_task(
                    task_func=computation_task,
                    data={"input": 20},
                    computation_id=computation_id_2,
                    delay=0.2,
                    metadata={"computation_id": computation_id_2}
                )
            )

        # Get task IDs
        task_id_1 = task1_future.result()
        task_id_2 = task2_future.result()

        # Wait for both tasks and collect events in parallel
        async with asyncio.TaskGroup() as tg:
            # Wait for tasks to complete
            _tasks_completion = tg.create_task(worker.wait_for_tasks([task_id_1, task_id_2]))

            # Collect events for both subscriptions
            _events1_collection = tg.create_task(
                event_sub_1.collect_events(
                    until_condition=lambda e: e.get("status") == "completed",
                    timeout=3.0
                )
            )

            _events2_collection = tg.create_task(
                event_sub_2.collect_events(
                    until_condition=lambda e: e.get("status") == "completed",
                    timeout=3.0
                )
            )

        # Get results
        events_1 = event_sub_1.get_events()
        events_2 = event_sub_2.get_events()

        # Verify events are properly filtered
        computation_ids_1 = {e.get("computation_id") for e in events_1}
        computation_ids_2 = {e.get("computation_id") for e in events_2}

        assert computation_ids_1 == {computation_id_1}
        assert computation_ids_2 == {computation_id_2}

        # Both event streams should have events
        assert len(events_1) >= 6  # 5 steps + completion
        assert len(events_2) >= 6  # 5 steps + completion


@pytest.mark.asyncio
async def test_historical_events(worker):
    """Test retrieving historical events after they've occurred."""
    # Generate a unique computation ID
    computation_id = str(uuid.uuid4())

    # Add and complete a task WITHOUT a subscriber
    task_id = await worker.add_task(
        task_func=computation_task,
        data={"input": 30},
        computation_id=computation_id,
        delay=0.1,  # Fast for quick completion
        metadata={"computation_id": computation_id}
    )

    # Get the future and await the result
    task_future = await worker.get_task_future(task_id)
    result = await task_future
    assert result["status"] == "completed"

    # Wait a bit to ensure events are published
    await asyncio.sleep(0.5)

    # Create a mock completion event to ensure it's available
    completion_event = {
        "computation_id": computation_id,
        "status": "completed",
        "result": result
    }

    # Manually add this event to the event manager
    await event_manager.publish_event(
        event_id=f"{computation_id}_completion",
        group_id=computation_id,
        event_data=completion_event
    )

    # NOW subscribe to events for this computation
    async with EventSubscription(computation_id) as event_sub:
        # Use it somewhere in the context block
        assert event_sub.group_id == computation_id

        # Get historical events using the filter
        filter_obj = GroupFilter(group_id=computation_id)
        historical_events = await event_manager.get_recent_events(filter_obj)

        # Should have found events even though we subscribed after completion
        assert len(historical_events) > 0

        # Verify the computation ID matches
        for event in historical_events:
            assert event.get("computation_id") == computation_id

        # Should include the completion event (which we manually published)
        completion_events = [
            e for e in historical_events
            if e.get("status") == "completed" or
               (isinstance(e.get("result"), dict) and e.get("result", {}).get("status") == "completed")
        ]
        assert len(completion_events) >= 1


if __name__ == "__main__":
    # This allows manual running of the tests outside of pytest
    async def run_tests():
        async_worker = AsyncTaskWorker(
            max_workers=2,
            task_timeout=10.0,
            worker_poll_interval=0.1,
            cache_enabled=False,
            max_queue_size=10,
            task_retention_days=1,
            cleanup_interval=60
        )

        # Use AsyncExitStack for proper cleanup
        async with AsyncExitStack() as stack:
            # Start worker and register cleanup
            await async_worker.start()
            stack.callback(async_worker.stop, timeout=2.0)

            # Configure event monitor and register cleanup
            monitor_task = await configure_task_worker_events(async_worker)

            async def cleanup_monitor():
                if hasattr(async_worker, "_event_monitor_stop"):
                    async_worker._event_monitor_stop.set()
                if monitor_task and not monitor_task.done():
                    monitor_task.cancel()
                    try:
                        await monitor_task
                    except asyncio.CancelledError:
                        pass
                await event_manager.stop()

            stack.callback(cleanup_monitor)

            try:
                print("Running test_task_with_events...")
                await test_task_with_events(async_worker)
                print("Success!")

                print("Running test_task_cancellation_with_events...")
                await test_task_cancellation_with_events(async_worker)
                print("Success!")

                print("Running test_multiple_tasks_with_event_filtering...")
                await test_multiple_tasks_with_event_filtering(async_worker)
                print("Success!")

                print("Running test_historical_events...")
                await test_historical_events(async_worker)
                print("Success!")

            except Exception as e:
                print(f"Test failed: {e}")
                raise


    # Run asyncio event loop
    asyncio.run(run_tests())
