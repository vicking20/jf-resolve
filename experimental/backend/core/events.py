"""Event bus system for addon communication and core decoupling"""

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from .logging import get_logger

logger = get_logger(__name__)


class EventPriority(Enum):
    """Event handler priority levels"""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class Event:
    """Base event class for all system events"""

    name: str
    data: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    source: str = "core"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Validate event on creation"""
        if not self.name:
            raise ValueError("Event name cannot be empty")
        if not isinstance(self.data, dict):
            raise ValueError("Event data must be a dictionary")


@dataclass
class EventHandler:
    """Wrapper for event handlers with metadata"""

    callback: Callable
    priority: EventPriority
    source: str  # addon_id or 'core'
    enabled: bool = True


class EventBus:
    """
    Central event bus for decoupled communication between core and addons

    Philosophy:
    - Core emits events, addons subscribe
    - Addons NEVER directly modify core behavior
    - Addons can suggest actions via events
    - Core decides whether to apply suggestions
    """

    def __init__(self):
        self._handlers: Dict[str, List[EventHandler]] = defaultdict(list)
        self._event_history: List[Event] = []
        self._max_history: int = 1000
        self._enabled: bool = True
        self._middleware: List[Callable] = []

    def subscribe(
        self,
        event_name: str,
        handler: Callable,
        priority: EventPriority = EventPriority.NORMAL,
        source: str = "core",
    ) -> None:
        """
        Subscribe to an event

        Args:
            event_name: Event to subscribe to (e.g., "library.item_added")
            handler: Async function to call when event occurs
            priority: Handler priority (higher = called first)
            source: Source identifier (addon_id or 'core')
        """
        if not asyncio.iscoroutinefunction(handler):
            raise ValueError("Event handlers must be async functions")

        event_handler = EventHandler(callback=handler, priority=priority, source=source)

        self._handlers[event_name].append(event_handler)

        # Sort by priority (highest first)
        self._handlers[event_name].sort(key=lambda h: h.priority.value, reverse=True)

        logger.info(
            f"Subscribed {source} to '{event_name}' with priority {priority.name}"
        )

    def unsubscribe(self, event_name: str, source: str) -> None:
        """Unsubscribe all handlers from a source for an event"""
        if event_name not in self._handlers:
            return

        self._handlers[event_name] = [
            h for h in self._handlers[event_name] if h.source != source
        ]

        logger.info(f"Unsubscribed {source} from '{event_name}'")

    def unsubscribe_all(self, source: str) -> None:
        """Unsubscribe a source from all events"""
        for event_name in list(self._handlers.keys()):
            self.unsubscribe(event_name, source)

    async def emit(self, event: Event) -> Dict[str, Any]:
        """
        Emit an event to all subscribers

        Args:
            event: Event to emit

        Returns:
            Dict with results from all handlers
        """
        if not self._enabled:
            logger.debug(f"Event bus disabled, ignoring event: {event.name}")
            return {}

        # Add to history
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history.pop(0)

        # Apply middleware
        for middleware in self._middleware:
            event = await middleware(event)

        logger.debug(f"Emitting event: {event.name} from {event.source}")

        handlers = self._handlers.get(event.name, [])
        enabled_handlers = [h for h in handlers if h.enabled]

        if not enabled_handlers:
            logger.debug(f"No handlers for event: {event.name}")
            return {}

        # Execute handlers in priority order
        results = {}
        for handler in enabled_handlers:
            try:
                result = await handler.callback(event)
                results[handler.source] = result
            except Exception as e:
                logger.error(
                    f"Error in event handler {handler.source} for {event.name}: {e}",
                    exc_info=True,
                )
                results[handler.source] = {"error": str(e)}

        return results

    def add_middleware(self, middleware: Callable) -> None:
        """Add middleware to process events before handlers"""
        if not asyncio.iscoroutinefunction(middleware):
            raise ValueError("Middleware must be async function")
        self._middleware.append(middleware)

    def enable_handler(self, event_name: str, source: str) -> None:
        """Enable a specific handler"""
        for handler in self._handlers.get(event_name, []):
            if handler.source == source:
                handler.enabled = True

    def disable_handler(self, event_name: str, source: str) -> None:
        """Disable a specific handler"""
        for handler in self._handlers.get(event_name, []):
            if handler.source == source:
                handler.enabled = False

    def get_event_names(self) -> Set[str]:
        """Get all registered event names"""
        return set(self._handlers.keys())

    def get_handlers(self, event_name: str) -> List[EventHandler]:
        """Get all handlers for an event"""
        return self._handlers.get(event_name, [])

    def get_history(self, limit: int = 100) -> List[Event]:
        """Get recent event history"""
        return self._event_history[-limit:]

    def clear_history(self) -> None:
        """Clear event history"""
        self._event_history.clear()

    def enable(self) -> None:
        """Enable event bus"""
        self._enabled = True

    def disable(self) -> None:
        """Disable event bus (for emergency kill switch)"""
        self._enabled = False
        logger.warning("Event bus disabled")


# Global event bus instance
event_bus = EventBus()


# Standard event names (for documentation and type safety)
class Events:
    """Standard event names used by core"""

    # Library events
    LIBRARY_ITEM_ADDED = "library.item_added"
    LIBRARY_ITEM_REMOVED = "library.item_removed"
    LIBRARY_ITEM_UPDATED = "library.item_updated"
    LIBRARY_SCAN_STARTED = "library.scan_started"
    LIBRARY_SCAN_COMPLETED = "library.scan_completed"

    # Playback events
    PLAYBACK_STARTED = "playback.started"
    PLAYBACK_STOPPED = "playback.stopped"
    PLAYBACK_PAUSED = "playback.paused"
    PLAYBACK_RESUMED = "playback.resumed"
    PLAYBACK_TIME_UPDATE = "playback.time_update"
    PLAYBACK_QUALITY_CHANGED = "playback.quality_changed"

    # Stream events
    STREAM_RESOLVED = "stream.resolved"
    STREAM_FAILED = "stream.failed"
    STREAM_FAILOVER = "stream.failover"

    # User events
    USER_REGISTERED = "user.registered"
    USER_LOGIN = "user.login"
    USER_LOGOUT = "user.logout"

    # Addon events
    ADDON_INSTALLED = "addon.installed"
    ADDON_ENABLED = "addon.enabled"
    ADDON_DISABLED = "addon.disabled"
    ADDON_UNINSTALLED = "addon.uninstalled"

    # System events
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"
    SYSTEM_ERROR = "system.error"


# Convenience functions
async def emit_event(
    name: str, data: Dict[str, Any], source: str = "core"
) -> Dict[str, Any]:
    """Emit an event (convenience function)"""
    event = Event(name=name, data=data, source=source)
    return await event_bus.emit(event)


def subscribe_to_event(
    event_name: str,
    handler: Callable,
    priority: EventPriority = EventPriority.NORMAL,
    source: str = "core",
) -> None:
    """Subscribe to an event (convenience function)"""
    event_bus.subscribe(event_name, handler, priority, source)
