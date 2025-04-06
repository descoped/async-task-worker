"""
Task Worker API Router

Factory function for creating a FastAPI router with endpoints for the AsyncTaskWorker system.
This allows for easy integration into any FastAPI application.

Example usage:
    from fastapi import FastAPI
    from contextlib import asynccontextmanager
    from task_worker import AsyncTaskWorker
    from task_worker.task_worker_api import create_task_worker_router

    # Create worker
    worker = AsyncTaskWorker()

    # Define application lifespan
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await worker.start()
        yield
        await worker.stop()

    # Create FastAPI app with lifespan
    app = FastAPI(lifespan=lifespan)

    # Create and include the router
    task_router = create_task_worker_router(worker)
    app.include_router(task_router)
"""

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from task_worker import (
    AsyncTaskWorker,
    TaskStatus,
    get_all_task_types,
    get_task_function,
)

logger = logging.getLogger(__name__)


# Request/Response Models
class TaskSubmitRequest(BaseModel):
    """Model for task submission requests"""
    task_type: str
    params: Dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    task_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    timeout: Optional[int] = None

    @classmethod
    @field_validator('task_type')
    def validate_task_type(cls, v):
        """Validate task type is registered"""
        if get_task_function(v) is None:
            raise ValueError(f"Task type '{v}' is not registered")
        return v


class TaskResponse(BaseModel):
    """Model for task responses"""
    id: str
    status: TaskStatus
    progress: float
    metadata: Dict[str, Any]
    result: Optional[Any] = None
    error: Optional[str] = None


class TaskListResponse(BaseModel):
    """Model for task list responses"""
    tasks: List[TaskResponse]
    count: int


class TaskTypesResponse(BaseModel):
    """Model for task types list response"""
    task_types: List[str]


class HealthResponse(BaseModel):
    """Model for health check response"""
    status: str
    worker_count: int


def create_task_worker_router(
        worker: AsyncTaskWorker,
        prefix: str = "",
        tags: Optional[List[str]] = None
) -> APIRouter:
    """
    Create a FastAPI router for the AsyncTaskWorker.

    Args:
        worker: The AsyncTaskWorker instance to use for task management
        prefix: Optional URL prefix for all routes (e.g., "/api/v1")
        tags: List of tags for API documentation (defaults to ["tasks"])

    Returns:
        A configured FastAPI router
    """
    if tags is None:
        tags = ["tasks"]

    router = APIRouter(prefix=prefix, tags=tags)

    # First define endpoints that could conflict with parameterized routes
    # Define the task types endpoint first to avoid path conflicts
    @router.get("/types", response_model=TaskTypesResponse)
    async def get_task_types() -> TaskTypesResponse:
        """Get a list of all registered task types"""
        task_types = get_all_task_types()
        return TaskTypesResponse(task_types=task_types)

    # Health check endpoint
    @router.get("/health", response_model=HealthResponse)
    async def health_check() -> HealthResponse:
        """Check the health of the task worker service"""
        return HealthResponse(
            status="ok" if worker.running else "stopped",
            worker_count=len(worker.workers),
        )

    # Now define the tasks endpoints
    @router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
    async def create_task(request: TaskSubmitRequest) -> TaskResponse:
        """Submit a new task for processing"""
        task_func = get_task_function(request.task_type)
        if task_func is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown task type: {request.task_type}",
            )

        # Add metadata about the task type
        metadata = request.metadata or {}
        metadata["task_type"] = request.task_type

        try:
            # Submit the task
            task_id = await worker.add_task(
                task_func,
                **request.params,
                priority=request.priority,
                task_id=request.task_id,
                metadata=metadata,
                timeout=request.timeout,
            )

            # Get task info
            task_info = worker.get_task_info(task_id)
            if task_info is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Task created but info not found",
                )

            return TaskResponse(
                id=task_info.id,
                status=task_info.status,
                progress=task_info.progress,
                metadata=task_info.metadata,
                result=task_info.result,
                error=task_info.error,
            )

        except Exception as e:
            logger.exception(f"Error submitting task: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to submit task: {str(e)}",
            )

    @router.get("/tasks/{task_id}", response_model=TaskResponse)
    async def get_task(task_id: str) -> TaskResponse:
        """Get information about a specific task by ID"""
        task_info = worker.get_task_info(task_id)
        if task_info is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task {task_id} not found",
            )

        return TaskResponse(
            id=task_info.id,
            status=task_info.status,
            progress=task_info.progress,
            metadata=task_info.metadata,
            result=task_info.result,
            error=task_info.error,
        )

    @router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def cancel_task(task_id: str) -> None:
        """Cancel a running or pending task"""
        task_info = worker.get_task_info(task_id)
        if task_info is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task {task_id} not found",
            )

        if task_info.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
            # Task already finished, just return success
            return

        cancelled = await worker.cancel_task(task_id)
        if not cancelled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Task {task_id} could not be cancelled",
            )

    @router.get("/tasks", response_model=TaskListResponse)
    async def list_tasks(
            task_status: Optional[TaskStatus] = None,
            limit: int = Query(50, ge=1, le=100),
            older_than_minutes: Optional[int] = Query(None, ge=0),
    ) -> TaskListResponse:
        """List tasks with optional filtering"""
        older_than = timedelta(minutes=older_than_minutes) if older_than_minutes else None

        tasks = worker.get_all_tasks(status=task_status, limit=limit, older_than=older_than)

        return TaskListResponse(
            tasks=[
                TaskResponse(
                    id=task.id,
                    status=task.status,
                    progress=task.progress,
                    metadata=task.metadata,
                    result=task.result,
                    error=task.error,
                )
                for task in tasks
            ],
            count=len(tasks),
        )

    return router
