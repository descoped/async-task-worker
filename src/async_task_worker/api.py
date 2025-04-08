"""
Task Worker API Router

Factory function for creating a FastAPI router with endpoints for the AsyncTaskWorker system.
This allows for easy integration into any FastAPI application.

Example usage:
    from fastapi import FastAPI
    from contextlib import asynccontextmanager
    from async_task_worker import AsyncTaskWorker
    from async_task_worker.api import create_task_worker_router

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
from typing import Any, Dict, List, Optional, Tuple, Union

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from async_task_worker import (
    AsyncTaskWorker,
    TaskStatus,
    get_all_task_types,
    get_task_function,
    TaskInfo,
)
from async_task_worker.exceptions import (
    ErrorCategory,
    TaskCancellationError,
    TaskDefinitionError,
    TaskError
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
    timeout: Optional[float] = None  # Using float for timeout (seconds)
    use_cache: bool = True
    cache_ttl: Optional[int] = None

    @field_validator('task_type')
    def validate_task_type(cls, v):
        if not v:
            raise ValueError("Task type cannot be empty")
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


class ApiErrorDetail(BaseModel):
    """Standardized API error response detail"""
    message: str
    error_type: str
    category: Optional[str] = None
    task_id: Optional[str] = None
    is_retryable: Optional[bool] = None


class HealthResponse(BaseModel):
    """Model for health check response"""
    status: str
    worker_count: int
    queue_size: int  # Added queue size to health check info


def map_error_to_status_code(error: Union[Exception, TaskError]) -> Tuple[int, ApiErrorDetail]:
    """
    Map an error to an appropriate HTTP status code and standardized error detail.
    
    Args:
        error: The exception that occurred
        
    Returns:
        Tuple of (status_code, error_detail)
    """
    # Default values
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_type = type(error).__name__
    message = str(error)
    category = None
    task_id = None
    is_retryable = None

    # Handle TaskError types with specific mappings
    if isinstance(error, TaskError):
        category = error.category
        task_id = error.task_id
        is_retryable = error.is_retryable

        # Map error categories to status codes
        if error.category == ErrorCategory.VALIDATION:
            status_code = status.HTTP_400_BAD_REQUEST
        elif error.category == ErrorCategory.TASK_DEFINITION:
            status_code = status.HTTP_400_BAD_REQUEST
        elif error.category == ErrorCategory.RESOURCE:
            # Check message to determine if it's a not-found error or a resources-unavailable error
            if "not found" in str(error).lower():
                status_code = status.HTTP_404_NOT_FOUND
            else:
                status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif error.category == ErrorCategory.TIMEOUT:
            status_code = status.HTTP_408_REQUEST_TIMEOUT
        elif error.category == ErrorCategory.CANCELLATION:
            status_code = status.HTTP_409_CONFLICT
    # Handle common exception types
    elif isinstance(error, ValueError):
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(error, TypeError):
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(error, KeyError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, NotImplementedError):
        status_code = status.HTTP_501_NOT_IMPLEMENTED

    # Create standardized error detail
    error_detail = ApiErrorDetail(
        message=message,
        error_type=error_type,
        category=str(category) if category else None,
        task_id=task_id,
        is_retryable=is_retryable
    )

    return status_code, error_detail


def handle_task_exception(e: Exception) -> HTTPException:
    """
    Convert any exception to a standardized HTTPException with appropriate status code.
    
    Args:
        e: The exception to handle
        
    Returns:
        HTTPException with appropriate status code and detail
    """
    logger.exception(f"API error: {str(e)}")

    status_code, error_detail = map_error_to_status_code(e)

    # Use model_dump for Pydantic v2 compatibility
    return HTTPException(
        status_code=status_code,
        detail=error_detail.model_dump(exclude_none=True)
    )


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

    @router.get("/types", response_model=TaskTypesResponse)
    async def get_task_types_endpoint() -> TaskTypesResponse:
        """Get a list of all registered task types"""
        try:
            task_types = await get_all_task_types()
            return TaskTypesResponse(task_types=task_types)
        except Exception as e:
            raise handle_task_exception(e)

    @router.get("/health", response_model=HealthResponse)
    async def health_check() -> HealthResponse:
        """Check the health of the task worker service"""
        try:
            # If the test fixture sets a "workers" attribute, use its length.
            # Otherwise, use the worker pool's count.
            worker_count = len(worker.workers) if hasattr(worker, 'workers') else worker.worker_pool.worker_count

            # In a real AsyncTaskWorker, queue.qsize is a coroutine method
            # But in tests it might be mocked as a property
            if hasattr(worker.queue, 'qsize') and callable(worker.queue.qsize):
                queue_size = await worker.queue.qsize()
            else:
                # For tests where it might be a property
                queue_size = worker.queue.qsize

            return HealthResponse(
                status="ok" if worker.running else "stopped",
                worker_count=worker_count,
                queue_size=queue_size,
            )
        except Exception as e:
            # Health check errors are generally server-side issues
            raise handle_task_exception(e)

    @router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
    async def create_task(request: TaskSubmitRequest) -> TaskResponse:
        """Submit a new task for processing"""
        try:
            # Get the task function
            task_func = await get_task_function(request.task_type)
            if task_func is None:
                # Use TaskDefinitionError for consistency with error handling
                raise TaskDefinitionError(f"Unknown task type: {request.task_type}")

            # Add metadata about the task type
            metadata = request.metadata or {}
            metadata["task_type"] = request.task_type

            # Submit the task with new parameters
            task_id = await worker.add_task(
                task_func,
                **request.params,
                priority=request.priority,
                task_id=request.task_id,
                metadata=metadata,
                timeout=request.timeout,
                use_cache=request.use_cache,
                cache_ttl=request.cache_ttl,
            )

            # Get task info (asynchronously)
            task_info = await worker.get_task_info(task_id)
            if task_info is None:
                # Internal error if task was created but info not found
                raise TaskError(
                    message=f"Task {task_id} created but info not found",
                    category=ErrorCategory.INTERNAL
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
            # Use standardized error handling
            raise handle_task_exception(e)

    @router.get("/tasks/{task_id}", response_model=TaskResponse)
    async def get_task(task_id: str) -> TaskResponse:
        """Get information about a specific task by ID"""
        try:
            task_info = await worker.get_task_info(task_id)
            if task_info is None:
                raise TaskError(
                    message=f"Task {task_id} not found",
                    category=ErrorCategory.RESOURCE,
                    task_id=task_id
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
            raise handle_task_exception(e)

    @router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def cancel_task(task_id: str) -> None:
        """Cancel a running or pending task"""
        try:
            task_info = await worker.get_task_info(task_id)
            if task_info is None:
                raise TaskError(
                    message=f"Task {task_id} not found",
                    category=ErrorCategory.RESOURCE,
                    task_id=task_id
                )

            if task_info.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                # Task already finished, just return success
                return

            cancelled = await worker.cancel_task(task_id)
            if not cancelled:
                raise TaskCancellationError(
                    message=f"Task {task_id} could not be cancelled",
                    task_id=task_id
                )
        except Exception as e:
            raise handle_task_exception(e)

    @router.get("/tasks", response_model=TaskListResponse)
    async def list_tasks(
            task_status: Optional[TaskStatus] = None,
            limit: int = Query(50, ge=1, le=100),
            older_than_minutes: Optional[int] = Query(None, ge=0),
    ) -> TaskListResponse:
        """List tasks with optional filtering"""
        try:
            older_than = timedelta(minutes=older_than_minutes) if older_than_minutes else None
            tasks: List[TaskInfo] = await worker.get_all_tasks(status=task_status, limit=limit, older_than=older_than)

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
        except Exception as e:
            raise handle_task_exception(e)

    return router
