from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from async_task_worker import AsyncTaskWorker, create_task_worker_router, task, TaskStatus


@pytest.fixture
async def test_app():
    """Create a FastAPI app with task worker router for testing."""
    app = FastAPI()
    worker = AsyncTaskWorker()

    # Register a test task
    @task("test_addition")
    async def add_numbers(a: int, b: int):
        return a + b

    router = create_task_worker_router(worker, prefix="/api/v1")
    app.include_router(router)

    # Don't actually start the worker, just set running state
    # (since we're mocking methods anyway)
    worker.running = True
    worker.workers = [MagicMock() for _ in range(3)]

    # Return TestClient and worker
    return TestClient(app), worker


@pytest.mark.asyncio
async def test_get_task_types(test_app):
    """Test the GET /types endpoint."""
    client, _ = test_app
    response = client.get("/api/v1/types")

    assert response.status_code == 200
    data = response.json()
    assert "task_types" in data
    assert "test_addition" in data["task_types"]


@pytest.mark.asyncio
async def test_health_check(test_app):
    """Test the GET /health endpoint."""
    client, worker = test_app
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["worker_count"] == 3


@pytest.mark.asyncio
async def test_create_task(test_app):
    """Test the POST /tasks endpoint."""
    client, worker = test_app

    # Mock the add_task method
    worker.add_task = AsyncMock(return_value="test-task-id")
    worker.get_task_info = MagicMock(return_value=MagicMock(
        id="test-task-id",
        status=TaskStatus.PENDING,
        progress=0.0,
        metadata={"task_type": "test_addition"},
        result=None,
        error=None
    ))

    # Submit task
    response = client.post(
        "/api/v1/tasks",
        json={
            "task_type": "test_addition",
            "params": {"a": 5, "b": 7},
            "priority": 1
        }
    )

    assert response.status_code == 201
    data = response.json()
    assert data["id"] == "test-task-id"
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_create_task_invalid_type(test_app):
    """Test creating a task with invalid task type."""
    client, _ = test_app

    # Test the API response for invalid task type
    response = client.post(
        "/api/v1/tasks",
        json={
            "task_type": "nonexistent_task",
            "params": {}
        }
    )

    assert response.status_code == 400  # Bad Request


@pytest.mark.asyncio
async def test_get_task(test_app):
    """Test the GET /tasks/{task_id} endpoint."""
    client, worker = test_app

    # Mock get_task_info method
    task_info = MagicMock(
        id="test-task-id",
        status=TaskStatus.COMPLETED,
        progress=1.0,
        metadata={"task_type": "test_addition"},
        result=12,
        error=None
    )
    worker.get_task_info = MagicMock(return_value=task_info)

    # Get task
    response = client.get("/api/v1/tasks/test-task-id")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "test-task-id"
    assert data["status"] == "completed"
    assert data["result"] == 12


@pytest.mark.asyncio
async def test_get_nonexistent_task(test_app):
    """Test getting a nonexistent task."""
    client, worker = test_app

    # Mock get_task_info to return None
    worker.get_task_info = MagicMock(return_value=None)

    # Get nonexistent task
    response = client.get("/api/v1/tasks/nonexistent-id")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cancel_task(test_app):
    """Test the DELETE /tasks/{task_id} endpoint."""
    client, worker = test_app

    # Mock methods
    task_info = MagicMock(
        id="test-task-id",
        status=TaskStatus.RUNNING
    )
    worker.get_task_info = MagicMock(return_value=task_info)
    worker.cancel_task = AsyncMock(return_value=True)

    # Cancel task
    response = client.delete("/api/v1/tasks/test-task-id")

    assert response.status_code == 204
    worker.cancel_task.assert_called_once_with("test-task-id")


@pytest.mark.asyncio
async def test_list_tasks(test_app):
    """Test the GET /tasks endpoint."""
    client, worker = test_app

    # Mock get_all_tasks method
    task1 = MagicMock(
        id="task-1",
        status=TaskStatus.COMPLETED,
        progress=1.0,
        metadata={"task_type": "test_addition"},
        result=12,
        error=None
    )
    task2 = MagicMock(
        id="task-2",
        status=TaskStatus.RUNNING,
        progress=0.5,
        metadata={"task_type": "test_addition"},
        result=None,
        error=None
    )
    worker.get_all_tasks = MagicMock(return_value=[task1, task2])

    # List all tasks
    response = client.get("/api/v1/tasks")

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 2
    assert len(data["tasks"]) == 2
    assert data["tasks"][0]["id"] == "task-1"
    assert data["tasks"][1]["id"] == "task-2"

    # Test with status filter - adding assertions for the response
    worker.get_all_tasks = MagicMock(return_value=[task2])  # Only return running task

    response = client.get("/api/v1/tasks?task_status=running")

    # Assert that get_all_tasks was called with the right parameters
    worker.get_all_tasks.assert_called_with(status=TaskStatus.RUNNING, limit=50, older_than=None)

    # Assert the response body contains the expected data
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["id"] == "task-2"
    assert data["tasks"][0]["status"] == "running"
