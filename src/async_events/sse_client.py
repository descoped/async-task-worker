"""
Modern SSE client using asyncio for reactive event processing.
"""
import asyncio
import json
import logging
from typing import Callable, Dict, List, Optional, Any, AsyncGenerator

from httpx import AsyncClient, Response

logger = logging.getLogger(__name__)


class SSEClient:
    """
    A modern Server-Sent Events client using asyncio for reactive design.

    Features:
    - True streaming connection (no polling)
    - Proper EventSource spec implementation
    - Async event handling with callbacks
    - Resource cleanup
    """

    def __init__(
            self,
            url: str,
            headers: Optional[Dict[str, str]] = None,
            event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
            timeout: int = 60
    ):
        self.url = url
        self.headers = headers or {}
        self.event_callback = event_callback
        self.timeout = timeout

        # Add required headers for SSE
        self.headers.update({
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        })

        # State management
        self._running = False
        self._task = None
        self._client = None
        self._events = []
        self._event_queue = asyncio.Queue()

    async def start(self) -> None:
        """Start the SSE client and connect to the server."""
        if self._running:
            return

        self._running = True
        self._client = AsyncClient(timeout=self.timeout)
        self._task = asyncio.create_task(self._connect_and_process())
        logger.info(f"SSE client started, connecting to {self.url}")

    async def _connect_and_process(self) -> None:
        """Establish connection and process the SSE stream."""
        try:
            logger.info(f"Establishing SSE connection to {self.url}")
            async with self._client.stream("GET", self.url, headers=self.headers) as response:
                response.raise_for_status()

                # Process the stream
                event_type = "message"  # Default event type
                event_data = ""
                event_id = None

                # Process the response as an async stream
                async for line in self._process_stream(response):
                    # Empty line means end of event
                    if not line:
                        if event_data:
                            await self._process_event(event_type, event_data, event_id)
                            event_type = "message"  # Reset to default
                            event_data = ""
                            event_id = None
                        continue

                    # Process field
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_part = line[5:].strip()
                        event_data = data_part if not event_data else f"{event_data}\n{data_part}"
                    elif line.startswith("id:"):
                        event_id = line[3:].strip()
                    elif line.startswith("retry:"):
                        # We could handle retry timing here
                        pass

                    # Ignore comments and other fields

        except asyncio.CancelledError:
            logger.info("SSE client task was cancelled")
        except Exception as e:
            logger.error(f"SSE connection error: {str(e)}", exc_info=True)
            # Only retry if we're still supposed to be running
            if self._running:
                logger.info("Reconnecting in 3 seconds...")
                await asyncio.sleep(3)
                asyncio.create_task(self._connect_and_process())

    @staticmethod
    async def _process_stream(response: Response) -> AsyncGenerator[str, None]:
        """Process the streaming response line by line."""
        buffer = ""
        async for chunk in response.aiter_text():
            buffer += chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                yield line

        # Handle any remaining content
        if buffer:
            yield buffer

    async def _process_event(self, event_type: str, data: str, event_id: Optional[str] = None) -> None:
        """Process a complete SSE event."""
        try:
            # Try to parse as JSON
            data_obj = json.loads(data)
            event = {
                "event": event_type,
                "data": data_obj,
                "id": event_id
            }

            # Store event
            self._events.append(event)

            # Add to queue
            await self._event_queue.put(event)

            # Call callback if provided
            if self.event_callback:
                self.event_callback(event_type, data_obj)

            logger.debug(f"Received SSE event: {event_type}")
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse event data as JSON: {data[:100]}")
        except Exception as e:
            logger.error(f"Error processing event: {str(e)}")

    async def stop(self) -> None:
        """Stop the SSE client and clean up resources."""
        if not self._running:
            return

        self._running = False

        # Cancel the task
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Close the client
        if self._client:
            await self._client.aclose()

        logger.info("SSE client stopped")

    async def get_event(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        Get the next event from the queue.

        Args:
            timeout: Maximum time to wait for an event (None = wait forever)

        Returns:
            Event dictionary or None if timeout
        """
        try:
            return await asyncio.wait_for(self._event_queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    def get_events(self) -> List[Dict[str, Any]]:
        """Get all events received so far."""
        return self._events

    async def wait_for_event(self, event_type: str, timeout: float = 30.0) -> Optional[Dict[str, Any]]:
        """
        Wait for a specific event type.

        Args:
            event_type: The type of event to wait for
            timeout: Maximum time to wait in seconds

        Returns:
            The event data or None if timeout
        """
        start_time = asyncio.get_event_loop().time()

        # First check existing events
        for event in self._events:
            if event["event"] == event_type:
                return event

        # Wait for new events
        while (asyncio.get_event_loop().time() - start_time) < timeout:
            try:
                remaining = timeout - (asyncio.get_event_loop().time() - start_time)
                event = await self.get_event(timeout=remaining)
                if event and event["event"] == event_type:
                    return event
            except asyncio.TimeoutError:
                break

        return None

    @property
    def is_running(self) -> bool:
        """Check if the client is running."""
        return self._running and self._task and not self._task.done()
