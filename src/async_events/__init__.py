"""
Async Events Package

This package provides utilities for asynchronous event handling and Server-Sent Events (SSE).
It includes a generic event management system and an SSE client implementation.

Components:
- EventManager: Generic pub/sub event system with filtering capabilities
- GroupFilter: Filter for matching events by group ID
- SSEClient: Client for consuming Server-Sent Events
"""

from async_events.event_manager import EventManager, EventFilter, GroupFilter, event_manager
from async_events.sse_client import SSEClient

__all__ = [
    "EventManager",
    "EventFilter",
    "GroupFilter",
    "event_manager",
    "SSEClient"
]
