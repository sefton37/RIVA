"""RIVA backend service.

Asyncio Unix domain socket server at ~/.talkingrock/riva.sock.
Accepts JSON-RPC 2.0 requests, dispatches via rpc_dispatcher.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import struct
from pathlib import Path

from cairn.settings import settings

from riva.rpc_dispatcher import dispatch
from riva.rpc_handlers.system import set_start_time
from riva.schema import ensure_schema

logger = logging.getLogger(__name__)

SOCKET_FILENAME = "riva.sock"


def get_socket_path() -> Path:
    """Return the path to the RIVA Unix domain socket."""
    return settings.data_dir / SOCKET_FILENAME


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Handle a single client connection.

    Protocol: length-prefixed JSON messages.
    Each message is preceded by a 4-byte big-endian uint32 indicating
    the length of the JSON payload that follows.
    """
    peer = writer.get_extra_info("peername") or "unknown"
    logger.debug("Client connected: %s", peer)

    try:
        while True:
            # Read 4-byte length prefix
            length_bytes = await reader.readexactly(4)
            msg_length = struct.unpack("!I", length_bytes)[0]

            if msg_length > 10 * 1024 * 1024:  # 10 MB sanity limit
                logger.warning("Message too large (%d bytes), closing connection", msg_length)
                break

            # Read the JSON payload
            data = await reader.readexactly(msg_length)
            raw = data.decode("utf-8")
            logger.debug("Received: %s", raw[:200])

            # Dispatch
            response = dispatch(raw)

            # Send length-prefixed response
            response_bytes = response.encode("utf-8")
            writer.write(struct.pack("!I", len(response_bytes)))
            writer.write(response_bytes)
            await writer.drain()

    except asyncio.IncompleteReadError:
        logger.debug("Client disconnected: %s", peer)
    except Exception:
        logger.exception("Error handling client %s", peer)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def _register_project_handlers() -> None:
    """Register project CRUD RPC handlers."""
    from riva.rpc_dispatcher import register_method
    from riva.rpc_handlers.projects import (
        handle_projects_archive,
        handle_projects_create,
        handle_projects_get,
        handle_projects_list,
        handle_projects_scan,
        handle_projects_update,
    )

    register_method("riva/projects/create", handle_projects_create)
    register_method("riva/projects/list", handle_projects_list)
    register_method("riva/projects/get", handle_projects_get)
    register_method("riva/projects/update", handle_projects_update)
    register_method("riva/projects/archive", handle_projects_archive)
    register_method("riva/projects/scan", handle_projects_scan)

    logger.info("Project handlers registered (6 methods)")


def _register_pm_handlers() -> None:
    """Register PM domain RPC handlers (epics, issues, cycles, roadmap, research)."""
    from riva.rpc_dispatcher import register_method
    from riva.rpc_handlers.pm import (
        handle_cycles_add_issue,
        handle_cycles_create,
        handle_cycles_get,
        handle_cycles_issues,
        handle_cycles_list,
        handle_cycles_remove_issue,
        handle_cycles_update,
        handle_dashboard,
        handle_epics_archive,
        handle_epics_create,
        handle_epics_get,
        handle_epics_list,
        handle_epics_update,
        handle_issues_create,
        handle_issues_get,
        handle_issues_list,
        handle_issues_update,
        handle_research_create,
        handle_research_get,
        handle_research_list,
        handle_research_update,
        handle_roadmap_create,
        handle_roadmap_get,
        handle_roadmap_link_epic,
        handle_roadmap_list,
        handle_roadmap_unlink_epic,
        handle_roadmap_update,
    )

    # Epics
    register_method("riva/pm/epics/list", handle_epics_list)
    register_method("riva/pm/epics/create", handle_epics_create)
    register_method("riva/pm/epics/get", handle_epics_get)
    register_method("riva/pm/epics/update", handle_epics_update)
    register_method("riva/pm/epics/archive", handle_epics_archive)

    # Issues
    register_method("riva/pm/issues/list", handle_issues_list)
    register_method("riva/pm/issues/create", handle_issues_create)
    register_method("riva/pm/issues/get", handle_issues_get)
    register_method("riva/pm/issues/update", handle_issues_update)

    # Cycles
    register_method("riva/pm/cycles/list", handle_cycles_list)
    register_method("riva/pm/cycles/create", handle_cycles_create)
    register_method("riva/pm/cycles/get", handle_cycles_get)
    register_method("riva/pm/cycles/update", handle_cycles_update)
    register_method("riva/pm/cycles/issues", handle_cycles_issues)
    register_method("riva/pm/cycles/add_issue", handle_cycles_add_issue)
    register_method("riva/pm/cycles/remove_issue", handle_cycles_remove_issue)

    # Roadmap
    register_method("riva/pm/roadmap/list", handle_roadmap_list)
    register_method("riva/pm/roadmap/create", handle_roadmap_create)
    register_method("riva/pm/roadmap/get", handle_roadmap_get)
    register_method("riva/pm/roadmap/update", handle_roadmap_update)
    register_method("riva/pm/roadmap/link_epic", handle_roadmap_link_epic)
    register_method("riva/pm/roadmap/unlink_epic", handle_roadmap_unlink_epic)

    # Research
    register_method("riva/pm/research/list", handle_research_list)
    register_method("riva/pm/research/create", handle_research_create)
    register_method("riva/pm/research/get", handle_research_get)
    register_method("riva/pm/research/update", handle_research_update)

    # Dashboard
    register_method("riva/pm/dashboard", handle_dashboard)

    logger.info("PM domain handlers registered (31 methods)")


def _register_devops_handlers() -> None:
    """Register DevOps RPC handlers (Forgejo + Woodpecker CI)."""
    from riva.devops.forgejo import ForgejoClient
    from riva.devops.woodpecker import WoodpeckerClient
    from riva.rpc_dispatcher import register_method
    from riva.rpc_handlers.devops import (
        handle_branches_list,
        handle_ci_logs,
        handle_ci_pipelines,
        handle_ci_repos,
        handle_ci_status,
        handle_ci_trigger,
        handle_commits_recent,
        handle_devops_status,
        handle_pr_create,
        handle_pr_list,
        handle_pr_merge,
        handle_repos_get,
        handle_repos_list,
        set_clients,
    )

    # Initialize clients from env vars
    forgejo = ForgejoClient()
    woodpecker = WoodpeckerClient()
    set_clients(forgejo, woodpecker)

    if forgejo.configured:
        logger.info("Forgejo client configured: %s", forgejo.base_url)
    else:
        logger.warning("Forgejo not configured (set FORGEJO_URL and FORGEJO_TOKEN)")

    if woodpecker.configured:
        logger.info("Woodpecker client configured: %s", woodpecker.base_url)
    else:
        logger.warning("Woodpecker not configured (set WOODPECKER_URL and WOODPECKER_TOKEN)")

    # Status
    register_method("riva/devops/status", handle_devops_status)

    # Forgejo repos
    register_method("riva/devops/repos/list", handle_repos_list)
    register_method("riva/devops/repos/get", handle_repos_get)
    register_method("riva/devops/commits/recent", handle_commits_recent)
    register_method("riva/devops/branches/list", handle_branches_list)

    # Forgejo PRs
    register_method("riva/devops/pr/list", handle_pr_list)
    register_method("riva/devops/pr/create", handle_pr_create)
    register_method("riva/devops/pr/merge", handle_pr_merge)

    # Woodpecker CI
    register_method("riva/devops/ci/repos", handle_ci_repos)
    register_method("riva/devops/ci/status", handle_ci_status)
    register_method("riva/devops/ci/pipelines", handle_ci_pipelines)
    register_method("riva/devops/ci/trigger", handle_ci_trigger)
    register_method("riva/devops/ci/logs", handle_ci_logs)

    logger.info("DevOps handlers registered (13 methods)")


async def start_server(
    *,
    socket_path: Path | None = None,
    ready_event: asyncio.Event | None = None,
) -> None:
    """Start the RIVA Unix socket server.

    Args:
        socket_path: Override socket path (for testing).
        ready_event: Set when the server is ready to accept connections.
    """
    sock_path = socket_path or get_socket_path()
    sock_path.parent.mkdir(parents=True, exist_ok=True)

    # Remove stale socket
    if sock_path.exists():
        sock_path.unlink()

    # Ensure schema and register handlers
    ensure_schema()
    set_start_time()
    _register_project_handlers()
    _register_pm_handlers()
    _register_devops_handlers()

    async def client_handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await _handle_client(reader, writer)

    server = await asyncio.start_unix_server(client_handler, path=str(sock_path))

    # Set permissions: owner-only
    os.chmod(sock_path, 0o600)

    logger.info("RIVA service listening on %s", sock_path)

    if ready_event is not None:
        ready_event.set()

    try:
        await server.serve_forever()
    except asyncio.CancelledError:
        logger.info("RIVA service shutting down")
    finally:
        server.close()
        await server.wait_closed()
        if sock_path.exists():
            sock_path.unlink()
        logger.info("RIVA service stopped")


def run_service() -> None:
    """Entry point for the RIVA service CLI."""
    import argparse

    parser = argparse.ArgumentParser(description="RIVA service")
    parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Handle SIGTERM gracefully
    loop = asyncio.new_event_loop()

    def _shutdown(sig: signal.Signals) -> None:
        logger.info("Received %s, shutting down...", sig.name)
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown, sig)

    try:
        loop.run_until_complete(start_server())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
