"""Tests for the RIVA properties store."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from riva.properties_store import (
    create_properties,
    get_effective_cli_args,
    get_properties,
    sync_to_disk,
    update_claude_md,
    update_permissions,
)
from riva.schema import ensure_schema


@pytest.fixture
def db_setup(tmp_path):
    """Set up test database with schema."""
    db_path = tmp_path / "talkingrock.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    conn.close()

    with patch("riva.db.settings") as mock_s, \
         patch("riva.properties_store.get_connection") as mock_get, \
         patch("riva.properties_store.transaction") as mock_tx:
        mock_s.data_dir = tmp_path

        def _get_conn(readonly=False):
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            return c

        mock_get.side_effect = _get_conn

        from contextlib import contextmanager

        @contextmanager
        def _tx():
            c = _get_conn()
            try:
                yield c
                c.commit()
            finally:
                c.close()

        mock_tx.side_effect = _tx

        yield tmp_path


class TestCreateProperties:
    """Tests for create_properties."""

    def test_creates_with_defaults(self, db_setup):
        """Creates properties with default CLAUDE.md template."""
        result = create_properties("agent-1", name="DevBot", purpose="Code assistant")
        assert result["id"].startswith("prop-")
        assert result["agent_id"] == "agent-1"
        assert "DevBot" in result["claude_md_content"]
        assert "Code assistant" in result["claude_md_content"]
        assert result["synced_at"] is None

    def test_creates_with_custom_claude_md(self, db_setup):
        """Custom CLAUDE.md overrides the template."""
        result = create_properties(
            "agent-2", claude_md="# Custom\nDo things."
        )
        assert result["claude_md_content"] == "# Custom\nDo things."

    def test_get_after_create(self, db_setup):
        """get_properties returns what was created."""
        create_properties("agent-3", name="Test")
        props = get_properties("agent-3")
        assert props is not None
        assert props["agent_id"] == "agent-3"
        assert "Test" in props["claude_md_content"]

    def test_get_nonexistent(self, db_setup):
        """get_properties returns None for unknown agent."""
        assert get_properties("agent-nonexistent") is None


class TestUpdateProperties:
    """Tests for update operations."""

    def test_update_claude_md(self, db_setup):
        """update_claude_md changes content and clears synced_at."""
        create_properties("agent-4", name="Bot")
        update_claude_md("agent-4", "# Updated\nNew content.")

        props = get_properties("agent-4")
        assert props["claude_md_content"] == "# Updated\nNew content."
        assert props["synced_at"] is None

    def test_update_permissions(self, db_setup):
        """update_permissions changes the permissions JSON."""
        create_properties("agent-5")
        update_permissions("agent-5", {"mode": "bypass", "allowed_tools": ["Read"]})

        props = get_properties("agent-5")
        perms = json.loads(props["permissions_json"])
        assert perms["mode"] == "bypass"
        assert "Read" in perms["allowed_tools"]

    def test_update_nonexistent_raises(self, db_setup):
        """Updating nonexistent agent raises PropertiesError."""
        from riva.errors import PropertiesError

        with pytest.raises(PropertiesError):
            update_claude_md("agent-nonexistent", "content")


class TestSyncToDisk:
    """Tests for sync_to_disk."""

    def test_writes_claude_md(self, db_setup):
        """sync_to_disk writes CLAUDE.md to the workspace."""
        workspace = db_setup / "workspace"
        workspace.mkdir()

        create_properties("agent-6", claude_md="# My Agent\nHello.")
        result = sync_to_disk("agent-6", str(workspace))

        assert result["synced"] is True
        assert (workspace / "CLAUDE.md").exists()
        assert (workspace / "CLAUDE.md").read_text() == "# My Agent\nHello."

    def test_sets_synced_at(self, db_setup):
        """After sync, synced_at is set."""
        workspace = db_setup / "workspace2"
        workspace.mkdir()

        create_properties("agent-7", claude_md="content")
        sync_to_disk("agent-7", str(workspace))

        props = get_properties("agent-7")
        assert props["synced_at"] is not None

    def test_detects_conflict(self, db_setup):
        """Conflict detected when disk differs from last sync."""
        workspace = db_setup / "workspace3"
        workspace.mkdir()

        create_properties("agent-8", claude_md="original content")
        sync_to_disk("agent-8", str(workspace))

        # Simulate manual edit on disk
        (workspace / "CLAUDE.md").write_text("manually edited content")

        # Now update in DB
        update_claude_md("agent-8", "new db content")

        # Sync again — should detect conflict
        # Note: synced_at was set by first sync, and disk content != db content
        # We need to re-set synced_at to simulate this properly
        # Actually, update_claude_md sets synced_at=NULL, so no conflict
        # Let's test the case where synced_at is still set
        # Force synced_at back
        from riva.db import get_connection

        conn = get_connection()
        conn.execute(
            "UPDATE riva_agent_properties SET synced_at='2026-01-01' "
            "WHERE agent_id='agent-8'"
        )
        conn.commit()
        conn.close()

        result = sync_to_disk("agent-8", str(workspace))
        assert result["conflict"] is True

    def test_no_conflict_on_fresh_sync(self, db_setup):
        """No conflict when file doesn't exist yet."""
        workspace = db_setup / "workspace4"
        workspace.mkdir()

        create_properties("agent-9", claude_md="content")
        result = sync_to_disk("agent-9", str(workspace))

        assert result["conflict"] is False


class TestCliArgs:
    """Tests for get_effective_cli_args."""

    def test_default_args(self, db_setup):
        """Default permissions produce --permission-mode acceptEdits."""
        create_properties("agent-10")
        args = get_effective_cli_args("agent-10")
        assert "--permission-mode" in args
        assert "acceptEdits" in args

    def test_bypass_mode(self, db_setup):
        """Bypass mode produces correct flag."""
        create_properties("agent-11")
        update_permissions("agent-11", {"mode": "bypass", "allowed_tools": []})
        args = get_effective_cli_args("agent-11")
        assert args == ["--permission-mode", "bypass"]

    def test_allowed_tools(self, db_setup):
        """Allowed tools are added as flags."""
        create_properties("agent-12")
        update_permissions(
            "agent-12",
            {"mode": "acceptEdits", "allowed_tools": ["Read", "Write"]},
        )
        args = get_effective_cli_args("agent-12")
        assert "--allowedTools" in args
        assert "Read" in args
        assert "Write" in args

    def test_nonexistent_agent(self, db_setup):
        """Returns empty list for unknown agent."""
        args = get_effective_cli_args("agent-nonexistent")
        assert args == []
