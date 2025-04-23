import asyncio
import logging
import time
import uuid
from typing import Dict, List, Optional, Set, Any, Tuple, Protocol, Generic, TypeVar

logger = logging.getLogger(__name__)

# Type variable for event data
T = TypeVar('T')


class EventFilter(Protocol):
    """Protocol for event filtering."""

    def match(self, event_data: Dict[str, Any]) -> bool:
        """
        Check if an event matches this filter.

        Args:
            event_data: The event data to check

        Returns:
            True if the event matches this filter
        """
        ...


class GroupFilter:
    """Filter events by group ID."""

    def __init__(self, group_id: str, group_id_field: str = "group_id"):
        """
        Initialize a group filter.

        Args:
            group_id: The group ID to filter on
            group_id_field: The field name containing the group ID
        """
        self.group_id = group_id
        self.group_id_field = group_id_field

    def match(self, event_data: Dict[str, Any]) -> bool:
        """
        Check if an event matches this group ID.

        Args:
            event_data: The event data to check

        Returns:
            True if the event belongs to this group
        """
        return event_data.get(self.group_id_field) == self.group_id


class EventManager(Generic[T]):
    """
    Generic event manager for application-wide event handling.

    Uses a pub/sub pattern with asyncio queues to propagate events
    to subscribers.
    """

    def __init__(self, result_ttl: int = 300):
        """
        Initialize the event manager.

        Args:
            result_ttl: Seconds to keep results in memory
        """
        # Map of subscriber_id -> queue for event delivery
        self.subscribers: Dict[str, asyncio.Queue] = {}

        # Map of group_id -> set of subscriber_ids
        self.group_subscriptions: Dict[str, Set[str]] = {}

        # Map of event_id -> (event_data, timestamp) for recent events
        self.recent_results: Dict[str, Tuple[T, float]] = {}

        # Set to track processed task IDs to avoid duplicates
        self.processed_tasks: Set[str] = set()

        # Task for cleanup
        self.cleanup_task = None
        self._running = False
        self.result_ttl = result_ttl  # Seconds to keep results in memory

        # Lock for thread-safe operations
        self.lock = asyncio.Lock()

        logger.info(f"{self.__class__.__name__} initialized")

    async def start(self):
        """Start the event manager."""
        if not self._running:
            self._running = True
            self.cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info(f"{self.__class__.__name__} started")

    async def stop(self):
        """Stop the event manager."""
        if self._running:
            self._running = False
            if self.cleanup_task:
                self.cleanup_task.cancel()
                try:
                    await self.cleanup_task
                except asyncio.CancelledError:
                    pass
            logger.info(f"{self.__class__.__name__} stopped")

    async def _cleanup_loop(self):
        """Background task to clean up stale data."""
        while self._running:
            try:
                # Clean up stale results
                now = time.time()
                to_remove = []

                async with self.lock:
                    # Clean up recent results older than TTL
                    for event_id, (result, timestamp) in list(self.recent_results.items()):
                        if now - timestamp > self.result_ttl:
                            to_remove.append(event_id)

                    for event_id in to_remove:
                        del self.recent_results[event_id]

                    # Clean processed tasks set periodically
                    if len(self.processed_tasks) > 10000:
                        self.processed_tasks.clear()
                        logger.info("Cleared processed tasks set")

                # Clean up empty group subscriptions
                empty_groups = []
                for group_id, subscribers in self.group_subscriptions.items():
                    if not subscribers:
                        empty_groups.append(group_id)

                for group_id in empty_groups:
                    async with self.lock:
                        self.group_subscriptions.pop(group_id, None)

                await asyncio.sleep(60)  # Run cleanup every minute

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in event manager cleanup: {str(e)}")
                await asyncio.sleep(60)  # Wait before retry

    async def subscribe(self, group_id: Optional[str] = None) -> Tuple[str, asyncio.Queue]:
        """
        Subscribe to events.

        Args:
            group_id: Optional group ID to filter events

        Returns:
            Tuple of (subscriber_id, queue)
        """
        subscriber_id = str(uuid.uuid4())
        queue = asyncio.Queue(maxsize=100)  # Limit queue size to prevent memory issues

        async with self.lock:
            self.subscribers[subscriber_id] = queue

            if group_id:
                if group_id not in self.group_subscriptions:
                    self.group_subscriptions[group_id] = set()
                self.group_subscriptions[group_id].add(subscriber_id)

        logger.info(f"New subscriber {subscriber_id} added for group {group_id}")
        return subscriber_id, queue

    async def unsubscribe(self, subscriber_id: str):
        """
        Unsubscribe from events.

        Args:
            subscriber_id: The subscriber ID to remove
        """
        async with self.lock:
            # Remove from subscribers dict
            if subscriber_id in self.subscribers:
                self.subscribers.pop(subscriber_id)

            # Remove from group subscriptions
            for group_id, subscribers in list(self.group_subscriptions.items()):
                if subscriber_id in subscribers:
                    subscribers.remove(subscriber_id)

        logger.debug(f"Subscriber {subscriber_id} removed")

    async def publish_event(self, event_id: str, group_id: str, event_data: Dict[str, Any]) -> int:
        """
        Publish an event to relevant subscribers.

        Args:
            event_id: ID of the event
            group_id: Group ID for routing
            event_data: Event data dictionary

        Returns:
            Number of subscribers notified
        """
        try:
            # Check if we've already processed this event
            async with self.lock:
                if event_id in self.processed_tasks:
                    logger.debug(f"Event {event_id} already processed, skipping")
                    return 0

                # Mark as processed first thing
                self.processed_tasks.add(event_id)

            # Add debug logging to trace event flow
            logger.info(f"Publishing event {event_id} for group {group_id}")

            # Store the event in recent results with timestamp
            async with self.lock:
                self.recent_results[event_id] = (event_data, time.time())
                logger.debug(f"Stored event {event_id} in recent_results")

            # Make sure group_id is in the event data
            if 'group_id' not in event_data:
                event_data['group_id'] = group_id

            # Get list of subscribers to notify
            subscribers_to_notify = set()

            async with self.lock:
                # Add group subscribers
                if group_id in self.group_subscriptions:
                    subscribers_to_notify.update(self.group_subscriptions[group_id])
                    logger.debug(f"Found {len(subscribers_to_notify)} subscribers for group {group_id}")
                else:
                    logger.debug(f"No subscribers found for group {group_id}")

            # Publish to all relevant queues
            notify_count = 0
            for subscriber_id in subscribers_to_notify:
                if subscriber_id in self.subscribers:
                    try:
                        # Use await to avoid blocking
                        await self.subscribers[subscriber_id].put(event_data)
                        notify_count += 1
                    except asyncio.QueueFull:
                        logger.warning(f"Queue full for subscriber {subscriber_id}, dropping event")

            logger.info(f"Published event {event_id} to {notify_count} subscribers")
            return notify_count

        except Exception as e:
            logger.error(f"Error publishing event: {str(e)}", exc_info=True)
            return 0

    async def get_recent_events(self, filter_func: EventFilter) -> List[Dict[str, Any]]:
        """
        Get recent events that match a filter.

        Args:
            filter_func: A function that takes event data and returns True if it matches

        Returns:
            List of matching events
        """
        events = []

        async with self.lock:
            for event_id, (event_data, timestamp) in self.recent_results.items():
                # Check if the event matches the filter
                if filter_func.match(event_data):
                    events.append(event_data)

        return events

    def is_processed(self, task_id: str) -> bool:
        """Check if a task has been processed already."""
        return task_id in self.processed_tasks


# Create a singleton event manager instance
event_manager = EventManager()
