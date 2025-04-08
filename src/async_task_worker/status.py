"""
Task Status Module

This module provides the task status management functionality,
including task state representation and transition handling.
"""
import asyncio
import logging
import time
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, ConfigDict, field_validator, PrivateAttr

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """
    Enumeration of possible task states.

    Using string enum ensures JSON-serializable values.
    """
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProgressUpdateStrategy:
    """
    Controls how progress updates are throttled and managed.
    
    Instead of acquiring a lock for every update, this class implements
    a rate-limited approach to progress tracking.
    """

    def __init__(self,
                 min_update_interval: float = 0.1,  # Minimum time between updates in seconds
                 min_progress_change: float = 0.02,  # Minimum change in progress to trigger an update
                 ):
        self.min_update_interval = min_update_interval
        self.min_progress_change = min_progress_change
        self.last_update_time = 0.0
        self.last_progress_value = 0.0

    def should_update(self, new_progress: float) -> bool:
        """
        Determine if a progress update should be processed.
        
        Uses a combination of time-based throttling and value-based delta checking.
        
        Args:
            new_progress: The new progress value (0.0 to 1.0)
            
        Returns:
            True if the update should be processed, False otherwise
        """
        current_time = time.time()
        time_since_update = current_time - self.last_update_time
        progress_change = abs(new_progress - self.last_progress_value)

        # Always update if this is the first update (time=0)
        if self.last_update_time == 0.0:
            self.last_update_time = current_time
            self.last_progress_value = new_progress
            return True

        # Always update on completion (progress=1.0)
        if new_progress == 1.0 and self.last_progress_value != 1.0:
            self.last_update_time = current_time
            self.last_progress_value = new_progress
            return True

        # Check if enough time has passed or enough progress has been made
        if (time_since_update >= self.min_update_interval or
                progress_change >= self.min_progress_change):
            self.last_update_time = current_time
            self.last_progress_value = new_progress
            return True

        return False


class TaskInfo(BaseModel):
    """
    Task information model for tracking task state.

    Contains all metadata about a task including its current status,
    timestamps, results, and custom metadata.
    """
    id: str
    status: TaskStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    progress: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Lock for synchronized state changes
    _lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)

    # Progress update throttling
    _progress_strategy: ProgressUpdateStrategy = PrivateAttr(default_factory=ProgressUpdateStrategy)

    # In-progress value for atomic updates
    _current_progress: float = PrivateAttr(default=0.0)

    # Pydantic v2 configuration
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
        extra="allow"
    )

    @field_validator("progress", mode="before")
    def validate_progress(cls, v: float) -> float:
        """Validate progress range"""
        if v < 0.0:
            return 0.0
        elif v > 1.0:
            return 1.0
        return v

    def is_terminal_state(self) -> bool:
        """
        Check if the task is in a terminal state.

        Returns:
            True if the task is completed, failed, or cancelled
        """
        return self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)

    def update_progress(self, progress: float) -> None:
        """
        Update task progress with throttling and validation.
        This is a lock-free operation that updates the internal progress value.
        The actual progress field update is governed by the ProgressUpdateStrategy.

        Args:
            progress: Progress value between 0.0 and 1.0
        """
        # Ensure progress is within bounds
        if progress < 0.0:
            progress = 0.0
        elif progress > 1.0:
            progress = 1.0

        # Update the atomic progress value
        self._current_progress = progress

        # If the update strategy says we should update the displayed progress,
        # then update the actual progress field which triggers validation
        if self._progress_strategy.should_update(progress):
            self.progress = progress

    async def mark_started(self) -> None:
        """
        Mark the task as started.
        """
        async with self._lock:
            self.status = TaskStatus.RUNNING
            self.started_at = datetime.now()

    async def mark_completed(self, result: Any) -> None:
        """
        Mark the task as successfully completed.

        Args:
            result: The result of the task
        """
        async with self._lock:
            if self.is_terminal_state():
                logger.warning(
                    f"Task {self.id} is already in a terminal state ({self.status}), cannot mark as completed.")
                return
            self.status = TaskStatus.COMPLETED
            self.completed_at = datetime.now()
            self.result = result

            # Ensure progress is set to 100% on completion
            self._current_progress = 1.0
            self.progress = 1.0

    async def mark_failed(self, error: str) -> None:
        """
        Mark the task as failed.

        Args:
            error: Error message or exception information
        """
        async with self._lock:
            self.status = TaskStatus.FAILED
            self.completed_at = datetime.now()
            self.error = error

    async def mark_cancelled(self, reason: Optional[str] = None) -> None:
        """
        Mark the task as cancelled.

        Args:
            reason: Optional reason for cancellation
        """
        async with self._lock:
            logger.info(f"Marking task {self.id} as cancelled: {reason}")
            self.status = TaskStatus.CANCELLED
            self.completed_at = datetime.now()
            self.error = reason

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert task info to a dictionary.

        Returns:
            Dictionary with task information
        """
        # Use the most up-to-date progress value, even if not yet in .progress
        current_progress = max(self.progress, self._current_progress)

        return {
            "id": self.id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "result": self.result,
            "error": self.error,
            "progress": current_progress,
            "metadata": self.metadata,
        }

    def get_current_progress(self) -> float:
        """
        Get the most up-to-date progress value, including any pending updates.
        
        Returns:
            The current progress as a float between 0.0 and 1.0
        """
        return max(self.progress, self._current_progress)
