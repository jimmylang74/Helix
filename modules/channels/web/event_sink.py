"""
SSE Event Sink — EventSink implementation via the status_events SSE bus.

Wraps modules.agent.status_events (thread-safe in-memory event bus) so the
orchestrator depends only on the HelixCore.interface.EventSink protocol.
Implements the HelixCore.interface.EventSink protocol (formerly direct
status_events.emit / status_events.cleanup calls).

The ``bus`` constructor argument is injectable for testing; it defaults to
the real status_events module.
"""

from typing import Any, Dict, List, Optional

from HelixCore.interface import EventSink


class SSEEventSink(EventSink):
    """Adapter that forwards emit/cleanup to the status_events SSE bus."""

    def __init__(self, bus: Any = None):
        if bus is None:
            from modules.agent import status_events as default_bus

            bus = default_bus
        self._bus = bus

    def emit(
        self,
        request_id: str,
        state: Dict[str, Any],
        graph_nodes: Optional[List[Dict[str, Any]]] = None,
        node_result: Optional[Dict[str, Any]] = None,
        completed: bool = False,
    ) -> None:
        """Forward a status snapshot to the SSE bus (dict params pass-through)."""
        self._bus.emit(
            request_id,
            state,
            graph_nodes=graph_nodes,
            node_result=node_result,
            completed=completed,
        )

    def cleanup(self, request_id: str) -> None:
        """Release all buffered events and consumer queues for ``request_id``."""
        self._bus.cleanup(request_id)
