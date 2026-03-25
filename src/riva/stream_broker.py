"""Stream Broker: pub/sub layer over CCManager event lists.

Solves the problem that CCManager buffers events in a list while
multiple consumers need them. The broker watches agent event lists
and pushes new events to subscriber queues.

For the Textual TUI: subscribers get asyncio.Queue objects (true push).
For the Tauri path: polling via riva/session/poll wraps CCManager.poll_events
directly — the broker is not involved.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Polling interval for watching CCManager event lists
_WATCH_INTERVAL = 0.05  # 50ms


class StreamBroker:
    """Pub/sub broker over CCManager agent event lists."""

    def __init__(self, manager: Any) -> None:
        """Initialize the broker.

        Args:
            manager: A CCManager instance whose poll_events() we watch.
        """
        self._manager = manager
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._watchers: dict[str, asyncio.Task] = {}
        self._on_done_callbacks: list[Any] = []

    def on_agent_done(self, callback) -> None:
        """Register a callback for when an agent completes.

        Called with (agent_id: str) when a 'done' event is observed.
        Used by the audit engine to auto-trigger audits.
        """
        self._on_done_callbacks.append(callback)

    def subscribe(self, agent_id: str) -> asyncio.Queue:
        """Subscribe to events for an agent.

        Returns an asyncio.Queue that will receive events as dicts.
        A 'done' event signals the stream is complete.

        Starts a watcher task if one isn't already running for this agent.
        """
        queue: asyncio.Queue = asyncio.Queue()

        if agent_id not in self._subscribers:
            self._subscribers[agent_id] = []

        self._subscribers[agent_id].append(queue)

        # Start watcher if not running
        if agent_id not in self._watchers or self._watchers[agent_id].done():
            self._watchers[agent_id] = asyncio.create_task(
                self._watch_agent(agent_id)
            )

        logger.debug(
            "Subscriber added for agent %s (total: %d)",
            agent_id,
            len(self._subscribers[agent_id]),
        )
        return queue

    def unsubscribe(self, agent_id: str, queue: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        subs = self._subscribers.get(agent_id, [])
        if queue in subs:
            subs.remove(queue)
        if not subs:
            self._subscribers.pop(agent_id, None)

    async def _watch_agent(self, agent_id: str) -> None:
        """Background task: poll CCManager events and push to subscribers."""
        last_index = 0

        try:
            while True:
                result = self._manager.poll_events(agent_id, since=last_index)
                events = result.get("events", [])
                new_index = result.get("next_index", last_index)
                busy = result.get("busy", False)

                for event in events:
                    await self._broadcast(agent_id, event)

                    if event.get("type") == "done":
                        # Notify done callbacks
                        for cb in self._on_done_callbacks:
                            try:
                                cb(agent_id)
                            except Exception:
                                logger.exception(
                                    "Error in done callback for %s", agent_id
                                )

                        # Cleanup
                        self._watchers.pop(agent_id, None)
                        logger.info("Watcher stopped for agent %s (done)", agent_id)
                        return

                last_index = new_index

                if not busy and not events:
                    # Agent is idle and no new events — stop watching
                    self._watchers.pop(agent_id, None)
                    return

                await asyncio.sleep(_WATCH_INTERVAL)

        except asyncio.CancelledError:
            logger.debug("Watcher cancelled for agent %s", agent_id)
        except Exception:
            logger.exception("Watcher error for agent %s", agent_id)

    async def _broadcast(self, agent_id: str, event: dict) -> None:
        """Push an event to all subscribers for an agent."""
        subs = self._subscribers.get(agent_id, [])
        for queue in subs:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "Subscriber queue full for agent %s, dropping event",
                    agent_id,
                )

    def stop(self) -> None:
        """Cancel all watcher tasks."""
        for task in self._watchers.values():
            task.cancel()
        self._watchers.clear()
        self._subscribers.clear()
