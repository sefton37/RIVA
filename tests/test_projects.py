"""Tests for RIVA project management RPC handlers."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from riva.rpc_handlers.projects import (
    handle_projects_archive,
    handle_projects_create,
    handle_projects_get,
    handle_projects_list,
    handle_projects_update,
)
from riva.schema import ensure_schema


@pytest.fixture
def db_setup(tmp_path):
    """Set up test database."""
    db_path = tmp_path / "talkingrock.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    conn.close()

    with patch("riva.db.settings") as mock_s, \
         patch("riva.rpc_handlers.projects.get_connection") as mock_get, \
         patch("riva.rpc_handlers.projects.transaction") as mock_tx:
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


class TestProjectsCRUD:
    """Tests for project CRUD operations."""

    def test_create_project(self, db_setup):
        result = handle_projects_create(name="My Project", description="Test desc")
        assert result["id"].startswith("proj-")
        assert result["name"] == "My Project"
        assert result["description"] == "Test desc"
        assert result["status"] == "active"

    def test_create_requires_name(self, db_setup):
        from riva.errors import RivaError

        with pytest.raises(RivaError):
            handle_projects_create(name="")

    def test_list_projects(self, db_setup):
        handle_projects_create(name="P1")
        handle_projects_create(name="P2")
        result = handle_projects_list()
        assert len(result["projects"]) == 2

    def test_list_filter_by_status(self, db_setup):
        p = handle_projects_create(name="Active")
        handle_projects_archive(project_id=p["id"])
        handle_projects_create(name="Still Active")

        active = handle_projects_list(status="active")
        assert len(active["projects"]) == 1
        assert active["projects"][0]["name"] == "Still Active"

    def test_get_project(self, db_setup):
        p = handle_projects_create(name="Get Me", description="details")
        result = handle_projects_get(project_id=p["id"])
        assert result["name"] == "Get Me"
        assert result["description"] == "details"

    def test_get_nonexistent(self, db_setup):
        from riva.errors import RivaError

        with pytest.raises(RivaError):
            handle_projects_get(project_id="proj-nonexistent")

    def test_update_project(self, db_setup):
        p = handle_projects_create(name="Original")
        result = handle_projects_update(project_id=p["id"], name="Updated")
        assert result["name"] == "Updated"

    def test_archive_project(self, db_setup):
        p = handle_projects_create(name="To Archive")
        result = handle_projects_archive(project_id=p["id"])
        assert result["status"] == "archived"

        # Verify in DB
        got = handle_projects_get(project_id=p["id"])
        assert got["status"] == "archived"

    def test_archive_already_archived(self, db_setup):
        from riva.errors import RivaError

        p = handle_projects_create(name="Double Archive")
        handle_projects_archive(project_id=p["id"])
        with pytest.raises(RivaError):
            handle_projects_archive(project_id=p["id"])
