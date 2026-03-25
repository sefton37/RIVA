"""Tests for DevOps clients and RPC handlers with mocked HTTP."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from riva.devops.forgejo import ForgejoClient
from riva.devops.woodpecker import WoodpeckerClient
from riva.errors import RivaError
from riva.rpc_handlers.devops import (
    handle_branches_list,
    handle_ci_pipelines,
    handle_ci_repos,
    handle_ci_status,
    handle_ci_trigger,
    handle_commits_recent,
    handle_devops_status,
    handle_pr_create,
    handle_pr_list,
    handle_repos_get,
    handle_repos_list,
    set_clients,
)


# ── Fixtures ─────────────────────────────────────────────────────────

_TEST_CRED = "test-value-not-real"


def _mock_response(json_data, status_code=200):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.status_code = status_code
    resp.raise_for_status.return_value = None
    return resp


@pytest.fixture
def forgejo():
    return ForgejoClient(base_url="http://test:3000", auth_token=_TEST_CRED, org="test-org")


@pytest.fixture
def woodpecker():
    return WoodpeckerClient(base_url="http://test:8880", auth_bearer=_TEST_CRED)


@pytest.fixture
def wired(forgejo, woodpecker):
    """Wire both clients into the RPC handlers."""
    set_clients(forgejo, woodpecker)
    yield
    set_clients(None, None)


# ── ForgejoClient Tests ──────────────────────────────────────────────


class TestForgejoClient:

    def test_configured(self, forgejo):
        assert forgejo.configured is True

    def test_not_configured(self):
        c = ForgejoClient(base_url="", auth_token="")
        assert c.configured is False

    @patch("riva.devops.forgejo.httpx.get")
    def test_list_repos(self, mock_get, forgejo):
        mock_get.return_value = _mock_response([
            {"name": "cairn", "full_name": "test-org/cairn"},
        ])
        repos = forgejo.list_repos()
        assert len(repos) == 1
        assert repos[0]["name"] == "cairn"
        mock_get.assert_called_once()

    @patch("riva.devops.forgejo.httpx.get")
    def test_list_commits(self, mock_get, forgejo):
        mock_get.return_value = _mock_response([
            {"sha": "abc123def456", "commit": {"message": "fix: thing\n\ndetails", "author": {"name": "k", "date": "2026-03-22"}}},
        ])
        commits = forgejo.list_commits("cairn", limit=5)
        assert len(commits) == 1
        assert commits[0]["sha"] == "abc123def456"

    @patch("riva.devops.forgejo.httpx.get")
    def test_list_pulls(self, mock_get, forgejo):
        mock_get.return_value = _mock_response([
            {"number": 1, "title": "Add feature", "state": "open"},
        ])
        pulls = forgejo.list_pulls("cairn")
        assert len(pulls) == 1
        assert pulls[0]["title"] == "Add feature"

    @patch("riva.devops.forgejo.httpx.post")
    def test_create_pull(self, mock_post, forgejo):
        mock_post.return_value = _mock_response({"number": 2, "title": "New PR"})
        result = forgejo.create_pull("cairn", title="New PR", head="feature")
        assert result["number"] == 2


# ── WoodpeckerClient Tests ───────────────────────────────────────────


class TestWoodpeckerClient:

    def test_configured(self, woodpecker):
        assert woodpecker.configured is True

    def test_not_configured(self):
        c = WoodpeckerClient(base_url="", auth_bearer="")
        assert c.configured is False

    @patch("riva.devops.woodpecker.httpx.get")
    def test_list_repos(self, mock_get, woodpecker):
        mock_get.return_value = _mock_response([
            {"id": 1, "full_name": "talking-rock/cairn", "active": True},
        ])
        repos = woodpecker.list_repos()
        assert len(repos) == 1
        assert repos[0]["id"] == 1

    @patch("riva.devops.woodpecker.httpx.get")
    def test_list_pipelines(self, mock_get, woodpecker):
        mock_get.return_value = _mock_response([
            {"number": 47, "status": "success", "branch": "main"},
        ])
        pipes = woodpecker.list_pipelines(1)
        assert len(pipes) == 1
        assert pipes[0]["status"] == "success"

    @patch("riva.devops.woodpecker.httpx.post")
    def test_trigger_pipeline(self, mock_post, woodpecker):
        mock_post.return_value = _mock_response({"number": 48, "status": "pending"})
        result = woodpecker.trigger_pipeline(1, branch="main")
        assert result["number"] == 48


# ── RPC Handler Tests ────────────────────────────────────────────────


class TestDevopsRpcHandlers:

    def test_status_unconfigured(self):
        set_clients(
            ForgejoClient(base_url="", auth_token=""),
            WoodpeckerClient(base_url="", auth_bearer=""),
        )
        result = handle_devops_status()
        assert result["forgejo"]["configured"] is False
        assert result["woodpecker"]["configured"] is False
        set_clients(None, None)

    @patch("riva.devops.forgejo.httpx.get")
    def test_repos_list(self, mock_get, wired):
        mock_get.return_value = _mock_response([
            {
                "name": "cairn",
                "full_name": "test-org/cairn",
                "description": "Attention minder",
                "default_branch": "main",
                "updated_at": "2026-03-22",
                "html_url": "http://test/cairn",
                "stars_count": 0,
                "open_issues_count": 2,
            },
        ])
        result = handle_repos_list()
        assert len(result["repos"]) == 1
        assert result["repos"][0]["name"] == "cairn"

    def test_repos_get_missing_param(self, wired):
        with pytest.raises(RivaError, match="repo is required"):
            handle_repos_get(repo="")

    @patch("riva.devops.forgejo.httpx.get")
    def test_commits_recent(self, mock_get, wired):
        mock_get.return_value = _mock_response([
            {"sha": "abc123def456789", "commit": {"message": "fix: bug\ndetails", "author": {"name": "k", "date": "2026-03-22"}}},
        ])
        result = handle_commits_recent(repo="cairn", limit=5)
        assert len(result["commits"]) == 1
        assert result["commits"][0]["sha"] == "abc123def456"
        assert result["commits"][0]["message"] == "fix: bug"

    @patch("riva.devops.forgejo.httpx.get")
    def test_branches_list(self, mock_get, wired):
        mock_get.return_value = _mock_response([
            {"name": "main", "protected": True},
            {"name": "feature", "protected": False},
        ])
        result = handle_branches_list(repo="cairn")
        assert len(result["branches"]) == 2

    @patch("riva.devops.forgejo.httpx.get")
    def test_pr_list(self, mock_get, wired):
        mock_get.return_value = _mock_response([
            {"number": 1, "title": "PR", "state": "open", "user": {"login": "k"},
             "head": {"ref": "feat"}, "base": {"ref": "main"}, "created_at": "2026-03-22",
             "html_url": "http://test/pr/1"},
        ])
        result = handle_pr_list(repo="cairn")
        assert len(result["pulls"]) == 1

    @patch("riva.devops.forgejo.httpx.post")
    def test_pr_create(self, mock_post, wired):
        mock_post.return_value = _mock_response({"number": 2, "title": "New"})
        result = handle_pr_create(repo="cairn", title="New", head="feat")
        assert result["number"] == 2

    def test_pr_create_missing_params(self, wired):
        with pytest.raises(RivaError, match="required"):
            handle_pr_create(repo="cairn", title="", head="")

    @patch("riva.devops.woodpecker.httpx.get")
    def test_ci_repos(self, mock_get, wired):
        mock_get.return_value = _mock_response([
            {"id": 1, "full_name": "test-org/cairn", "active": True},
        ])
        result = handle_ci_repos()
        assert len(result["repos"]) == 1

    @patch("riva.devops.woodpecker.httpx.get")
    def test_ci_status(self, mock_get, wired):
        mock_get.return_value = _mock_response([
            {"number": 47, "status": "success", "event": "push", "branch": "main",
             "message": "fix: thing", "started_at": 1, "finished_at": 2},
        ])
        result = handle_ci_status(repo_id=1)
        assert result["latest"]["status"] == "success"

    @patch("riva.devops.woodpecker.httpx.get")
    def test_ci_status_empty(self, mock_get, wired):
        mock_get.return_value = _mock_response([])
        result = handle_ci_status(repo_id=1)
        assert result["latest"] is None

    def test_ci_status_missing_param(self, wired):
        with pytest.raises(RivaError, match="repo_id is required"):
            handle_ci_status(repo_id=0)

    @patch("riva.devops.woodpecker.httpx.get")
    def test_ci_pipelines(self, mock_get, wired):
        mock_get.return_value = _mock_response([
            {"number": 47, "status": "success", "event": "push", "branch": "main",
             "message": "msg", "started_at": 1, "finished_at": 2},
        ])
        result = handle_ci_pipelines(repo_id=1)
        assert len(result["pipelines"]) == 1

    @patch("riva.devops.woodpecker.httpx.post")
    def test_ci_trigger(self, mock_post, wired):
        mock_post.return_value = _mock_response({"number": 48, "status": "pending"})
        result = handle_ci_trigger(repo_id=1, branch="main")
        assert result["number"] == 48

    def test_ci_trigger_missing_param(self, wired):
        with pytest.raises(RivaError, match="repo_id is required"):
            handle_ci_trigger(repo_id=0)
