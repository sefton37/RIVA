"""Tests for the RIVA stream broker."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from riva.stream_broker import StreamBroker


@pytest.fixture
def mock_manager():
    """Mock CCManager with controllable event list."""
    manager = MagicMock()
    # Start with empty events
    manager._events = []
    manager._busy = True

    def poll_events(agent_id, since=0):
        events = manager._events[since:]
        return {
            "events": events,
            "next_index": len(manager._events),
            "busy": manager._busy,
        }

    manager.poll_events.side_effect = poll_events
    return manager


class TestStreamBroker:
    """Tests for StreamBroker pub/sub."""

    @pytest.mark.asyncio
    async def test_subscribe_returns_queue(self, mock_manager):
        """subscribe() returns an asyncio.Queue."""
        broker = StreamBroker(mock_manager)
        queue = broker.subscribe("agent-1")
        assert isinstance(queue, asyncio.Queue)
        broker.stop()

    @pytest.mark.asyncio
    async def test_events_pushed_to_subscriber(self, mock_manager):
        """Events from CCManager are pushed to subscriber queues."""
        broker = StreamBroker(mock_manager)
        queue = broker.subscribe("agent-1")

        # Give watcher time to start
        await asyncio.sleep(0.02)

        # Add events
        mock_manager._events.append({"type": "assistant_delta", "text": "Hello"})
        mock_manager._events.append({"type": "done"})

        # Wait for events to propagate
        events = []
        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
                events.append(event)
                if event.get("type") == "done":
                    break
        except asyncio.TimeoutError:
            pass

        assert len(events) >= 2
        assert events[0]["type"] == "assistant_delta"
        assert events[0]["text"] == "Hello"
        assert events[-1]["type"] == "done"
        broker.stop()

    @pytest.mark.asyncio
    async def test_done_event_stops_watcher(self, mock_manager):
        """Watcher stops after a 'done' event."""
        broker = StreamBroker(mock_manager)
        broker.subscribe("agent-1")

        await asyncio.sleep(0.02)

        mock_manager._events.append({"type": "done"})
        await asyncio.sleep(0.2)

        # Watcher should be gone
        assert "agent-1" not in broker._watchers or broker._watchers["agent-1"].done()
        broker.stop()

    @pytest.mark.asyncio
    async def test_done_callback_fires(self, mock_manager):
        """on_agent_done callback fires when done event observed."""
        broker = StreamBroker(mock_manager)

        done_agents = []
        broker.on_agent_done(lambda aid: done_agents.append(aid))

        broker.subscribe("agent-2")
        await asyncio.sleep(0.02)

        mock_manager._events.append({"type": "done"})
        await asyncio.sleep(0.2)

        assert "agent-2" in done_agents
        broker.stop()

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self, mock_manager):
        """Multiple subscribers all receive the same events."""
        broker = StreamBroker(mock_manager)
        q1 = broker.subscribe("agent-3")
        q2 = broker.subscribe("agent-3")

        await asyncio.sleep(0.02)

        mock_manager._events.append({"type": "assistant_delta", "text": "Hi"})
        mock_manager._events.append({"type": "done"})

        await asyncio.sleep(0.3)

        # Both queues should have events
        assert not q1.empty()
        assert not q2.empty()
        broker.stop()

    @pytest.mark.asyncio
    async def test_unsubscribe(self, mock_manager):
        """unsubscribe removes a queue."""
        broker = StreamBroker(mock_manager)
        queue = broker.subscribe("agent-4")
        broker.unsubscribe("agent-4", queue)

        assert len(broker._subscribers.get("agent-4", [])) == 0
        broker.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_watchers(self, mock_manager):
        """stop() cancels all watcher tasks."""
        broker = StreamBroker(mock_manager)
        broker.subscribe("agent-5")

        await asyncio.sleep(0.02)
        broker.stop()

        assert len(broker._watchers) == 0
        assert len(broker._subscribers) == 0

    @pytest.mark.asyncio
    async def test_events_in_order(self, mock_manager):
        """Events arrive in the same order they were added."""
        broker = StreamBroker(mock_manager)
        queue = broker.subscribe("agent-6")

        await asyncio.sleep(0.02)

        for i in range(5):
            mock_manager._events.append({"type": "assistant_delta", "text": f"msg-{i}"})
        mock_manager._events.append({"type": "done"})

        events = []
        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
                events.append(event)
                if event.get("type") == "done":
                    break
        except asyncio.TimeoutError:
            pass

        texts = [e["text"] for e in events if e.get("type") == "assistant_delta"]
        assert texts == ["msg-0", "msg-1", "msg-2", "msg-3", "msg-4"]
        broker.stop()
