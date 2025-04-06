import unittest
from typing import Any

from async_task_worker import (
    get_all_task_types,
    get_task_function,
    register_task,
    task,
)


# Test task_registry.py
class TestTaskRegistry(unittest.IsolatedAsyncioTestCase):
    """Test the task registry functionality."""

    def setUp(self):
        # Clear task registry before each test
        from async_task_worker.task_registry import _TASK_REGISTRY
        _TASK_REGISTRY.clear()

    async def test_task_decorator(self):
        """Test task decorator registers a function correctly."""

        @task("test_task")
        async def test_func(x, y):
            return x + y

        # Verify task was registered
        self.assertIsNotNone(get_task_function("test_task"))

        # Verify function still works
        result = await test_func(5, 7)
        self.assertEqual(result, 12)

    async def test_register_task(self):
        """Test manual task registration."""

        async def another_task(a, b, c):
            return a * b * c

        register_task("multiply", another_task)

        # Verify task was registered
        task_func = get_task_function("multiply")
        self.assertIsNotNone(task_func)

        # Verify function reference is correct
        result = await task_func(2, 3, 4)
        self.assertEqual(result, 24)

    def test_get_all_task_types(self):
        """Test retrieving all registered task types."""

        # Register some test tasks
        @task("task1")
        async def task1():
            pass

        @task("task2")
        async def task2():
            pass

        # Get all task types
        task_types = get_all_task_types()

        # Verify both tasks are registered
        self.assertIn("task1", task_types)
        self.assertIn("task2", task_types)
        self.assertEqual(len(task_types), 2)

    def test_register_non_async_task(self):
        """Test that registering a non-async function raises TypeError."""

        # Define the function with proper type annotation to make static type checkers happy
        # but it's still a sync function that will be caught at runtime
        def sync_func() -> Any:  # Using Any instead of specific return type to avoid type checking errors
            return "not async"

        # This should still raise TypeError at runtime despite the return annotation
        with self.assertRaises(TypeError):
            register_task("sync_task", sync_func)  # type: ignore

    def test_task_decorator_with_non_async(self):
        """Test that task decorator raises TypeError when applied to a non-async function."""

        with self.assertRaises(TypeError):
            @task("invalid_task")  # type: ignore
            def not_async_func(x):
                return x * 2


if __name__ == "__main__":
    unittest.main()
