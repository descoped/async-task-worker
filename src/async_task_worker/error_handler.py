"""
Error Handler Module

This module provides a unified error hierarchy for the task worker system.
"""

import asyncio
import logging
import traceback
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ErrorCategory(str, Enum):
    """Categories of errors for classification and handling."""
    TASK_DEFINITION = "task_definition"
    TASK_EXECUTION = "task_execution"
    TIMEOUT = "timeout"
    CANCELLATION = "cancellation"
    RESOURCE = "resource"
    VALIDATION = "validation"
    INTERNAL = "internal"
    UNKNOWN = "unknown"


class SerializationError(Exception):
    """Exception raised when serialization fails."""
    pass


class TaskError(Exception):
    """Base exception for all task worker errors."""

    def __init__(
            self,
            message: str,
            category: ErrorCategory = ErrorCategory.UNKNOWN,
            original_error: Optional[Exception] = None,
            task_id: Optional[str] = None,
            is_retryable: bool = False
    ):
        self.message = message
        self.category = category
        self.original_error = original_error
        self.task_id = task_id
        self.is_retryable = is_retryable
        super().__init__(message)

    def __str__(self) -> str:
        base_msg = f"[{self.category}] {self.message}"
        if self.task_id:
            base_msg = f"Task {self.task_id}: {base_msg}"
        return base_msg

    def to_dict(self) -> Dict[str, Any]:
        """Convert error to a dictionary representation."""
        result = {
            "message": self.message,
            "category": self.category,
            "is_retryable": self.is_retryable,
        }

        if self.task_id:
            result["task_id"] = self.task_id

        if self.original_error:
            result["original_error"] = str(self.original_error)
            result["original_type"] = type(self.original_error).__name__

        return result


class TaskDefinitionError(TaskError):
    """Error in task definition or registration."""

    def __init__(self, message: str, original_error: Optional[Exception] = None, task_id: Optional[str] = None):
        super().__init__(
            message=message,
            category=ErrorCategory.TASK_DEFINITION,
            original_error=original_error,
            task_id=task_id,
            is_retryable=False
        )


class TaskExecutionError(TaskError):
    """Error during task execution."""

    def __init__(
            self,
            message: str,
            original_error: Optional[Exception] = None,
            task_id: Optional[str] = None,
            is_retryable: bool = True
    ):
        super().__init__(
            message=message,
            category=ErrorCategory.TASK_EXECUTION,
            original_error=original_error,
            task_id=task_id,
            is_retryable=is_retryable
        )


class TaskTimeoutError(TaskError):
    """Error when a task times out."""

    def __init__(self, timeout: float, task_id: Optional[str] = None):
        super().__init__(
            message=f"Task timed out after {timeout} seconds",
            category=ErrorCategory.TIMEOUT,
            task_id=task_id,
            is_retryable=True
        )


class TaskCancellationError(TaskError):
    """Error when a task is cancelled."""

    def __init__(self, message: str = "Task was cancelled", task_id: Optional[str] = None):
        super().__init__(
            message=message,
            category=ErrorCategory.CANCELLATION,
            task_id=task_id,
            is_retryable=False
        )


class ErrorHandler:
    """
    Centralized error handler for task worker.

    Provides consistent error handling, logging, and categorization.
    """

    def __init__(self, structured_logging: bool = False):
        """
        Initialize the error handler.

        Args:
            structured_logging: Whether to use structured logging format
        """
        self.structured_logging = structured_logging

    def handle_error(
            self,
            error: Exception,
            task_id: Optional[str] = None,
            context: Optional[Dict[str, Any]] = None
    ) -> TaskError:
        """
        Handle an error and return a standardized TaskError.

        Args:
            error: The exception that occurred
            task_id: Optional task ID for context
            context: Additional context information

        Returns:
            Standardized TaskError instance
        """
        # Default context if none provided
        if context is None:
            context = {}

        # If already a TaskError, just add task_id if needed
        if isinstance(error, TaskError):
            if task_id and not error.task_id:
                error.task_id = task_id
            self._log_error(error, context)
            return error

        # Map standard exceptions to appropriate TaskError types
        if isinstance(error, asyncio.TimeoutError):
            task_error = TaskTimeoutError(
                timeout=context.get("timeout", 0),
                task_id=task_id
            )
        elif isinstance(error, asyncio.CancelledError):
            task_error = TaskCancellationError(task_id=task_id)
        elif isinstance(error, (TypeError, ValueError, AttributeError)):
            task_error = TaskDefinitionError(
                message=str(error),
                original_error=error,
                task_id=task_id
            )
        else:
            # Default to generic execution error
            task_error = TaskExecutionError(
                message=str(error),
                original_error=error,
                task_id=task_id
            )

        self._log_error(task_error, context)
        return task_error

    def _log_error(self, error: TaskError, context: Dict[str, Any]) -> None:
        """
        Log an error with appropriate level and format.

        Args:
            error: The error to log
            context: Additional context information
        """
        # Determine log level based on category
        if error.category in (ErrorCategory.CANCELLATION, ErrorCategory.TIMEOUT):
            log_level = logging.INFO
        elif error.category == ErrorCategory.VALIDATION:
            log_level = logging.WARNING
        else:
            log_level = logging.ERROR

        # Create log message
        if self.structured_logging:
            log_data = {
                "error": error.to_dict(),
                "context": context
            }
            logger.log(log_level, log_data)
        else:
            message = str(error)
            if error.original_error and log_level >= logging.ERROR:
                logger.log(log_level, message, exc_info=error.original_error)
            else:
                logger.log(log_level, message)

    @staticmethod
    def format_exception(exc: Exception) -> str:
        """
        Format an exception with traceback into a string.

        Args:
            exc: The exception to format

        Returns:
            Formatted exception string with traceback
        """
        if exc.__traceback__:
            tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
            return ''.join(tb_lines)
        else:
            return f"{type(exc).__name__}: {str(exc)}"
