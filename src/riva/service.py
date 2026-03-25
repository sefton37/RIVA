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

from trcore.settings import settings

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
    *,
    provider: object | None = None,
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
            response = dispatch(raw, provider=provider)

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


def _register_phase2_handlers(
    provider: object | None = None, *, structured: bool = False
) -> None:
    """Register Phase 2 RPC handlers (plan engine + contracts).

    Args:
        provider: LLM provider for plan decomposition.
        structured: Use StructuredPlanner (multi-stage extraction)
            instead of PlanEngine (single-shot JSON generation).
    """
    from riva.rpc_dispatcher import register_method
    from riva.rpc_handlers.contracts import (
        handle_contract_cancel,
        handle_contract_get,
        handle_contract_list,
    )
    from riva.rpc_handlers.plans import (
        handle_plan_approve,
        handle_plan_create,
        handle_plan_get,
        handle_plan_list,
        handle_plan_status,
        set_engine,
    )

    if structured:
        from riva.structured_planner import StructuredPlanner
        engine = StructuredPlanner(provider=provider)
        logger.info("Using STRUCTURED plan pipeline (multi-stage extraction)")
    else:
        from riva.plan_engine import PlanEngine
        engine = PlanEngine(provider=provider)
        logger.info("Using FREE-FORM plan engine (single-shot JSON)")

    set_engine(engine)

    # Plan methods
    register_method("riva/plan/create", handle_plan_create, guarded=True)
    register_method("riva/plan/status", handle_plan_status)
    register_method("riva/plan/get", handle_plan_get)
    register_method("riva/plan/list", handle_plan_list)
    register_method("riva/plan/approve", handle_plan_approve)

    # Contract methods
    register_method("riva/contract/get", handle_contract_get)
    register_method("riva/contract/list", handle_contract_list)
    register_method("riva/contract/cancel", handle_contract_cancel)

    logger.info("Phase 2 handlers registered (plan engine + contracts)")


def _register_phase3_handlers(*, simulated: bool = False) -> None:
    """Register Phase 3 RPC handlers (agent observatory + properties).

    Args:
        simulated: Use SimCCManager instead of real CCManager.
            Simulated mode writes expected files to workspace without
            spawning Claude Code. Safe for testing.
    """
    from riva.cc_adapter import RivaCCDatabase
    from riva.rpc_dispatcher import register_method
    from riva.rpc_handlers.agents import (
        handle_agents_create,
        handle_agents_delete,
        handle_agents_get,
        handle_agents_list,
        handle_properties_get,
        handle_properties_sync,
        handle_properties_update,
        set_manager,
    )

    cc_db = RivaCCDatabase()

    if simulated:
        from riva.sim_cc_manager import SimCCManager
        manager = SimCCManager(db=cc_db)
        logger.info("Using SIMULATED CCManager (no real Claude Code)")
    else:
        from trcore.cc_manager import CCManager
        manager = CCManager(db=cc_db)
        logger.info("Using REAL CCManager (Claude Code CLI)")

    set_manager(manager)

    # Agent methods
    register_method("riva/agents/list", handle_agents_list)
    register_method("riva/agents/get", handle_agents_get)
    register_method("riva/agents/create", handle_agents_create)
    register_method("riva/agents/delete", handle_agents_delete)

    # Properties methods
    register_method("riva/agents/properties/get", handle_properties_get)
    register_method("riva/agents/properties/update", handle_properties_update)
    register_method("riva/agents/properties/sync", handle_properties_sync)

    logger.info("Phase 3 handlers registered (agent observatory + properties)")

    return manager  # Return manager for Phase 4


def _register_phase4_handlers(manager) -> None:
    """Register Phase 4 RPC handlers (deployment + live streaming)."""
    from riva.rpc_dispatcher import register_method
    from riva.rpc_handlers.sessions import (
        handle_session_deploy,
        handle_session_history,
        handle_session_poll,
        handle_session_stop,
        set_broker,
    )
    from riva.rpc_handlers.sessions import (
        set_manager as set_session_manager,
    )
    from riva.stream_broker import StreamBroker

    # Share the same CCManager from Phase 3
    set_session_manager(manager)

    # Initialize stream broker
    broker = StreamBroker(manager)
    set_broker(broker)

    # Session methods
    register_method("riva/session/deploy", handle_session_deploy)
    register_method("riva/session/poll", handle_session_poll)
    register_method("riva/session/stop", handle_session_stop)
    register_method("riva/session/history", handle_session_history)

    logger.info("Phase 4 handlers registered (deployment + live streaming)")

    return broker


def _register_phase5_handlers(manager, broker) -> None:
    """Register Phase 5 RPC handlers (audit engine + project management)."""
    from riva.rpc_dispatcher import register_method
    from riva.rpc_handlers.audits import (
        handle_audit_get,
        handle_audit_list,
        handle_audit_trigger,
    )
    from riva.rpc_handlers.audits import (
        set_manager as set_audit_manager,
    )
    from riva.rpc_handlers.projects import (
        handle_projects_archive,
        handle_projects_create,
        handle_projects_get,
        handle_projects_list,
        handle_projects_update,
    )

    set_audit_manager(manager)

    # Audit methods
    register_method("riva/audit/trigger", handle_audit_trigger)
    register_method("riva/audit/get", handle_audit_get)
    register_method("riva/audit/list", handle_audit_list)

    # Project methods
    register_method("riva/projects/create", handle_projects_create)
    register_method("riva/projects/list", handle_projects_list)
    register_method("riva/projects/get", handle_projects_get)
    register_method("riva/projects/update", handle_projects_update)
    register_method("riva/projects/archive", handle_projects_archive)

    # Wire auto-audit on agent done event
    def _on_agent_done(agent_id: str) -> None:
        """Auto-trigger audit when an agent completes with an active contract."""
        import os

        from riva.contract_store import list_contracts

        username = os.environ.get("USER", "unknown")
        agents = manager.list_agents(username)
        agent = next((a for a in agents if a["id"] == agent_id), None)
        if agent is None:
            return

        active = list_contracts(status="active")
        for contract in active:
            if contract.agent_id == agent_id:
                try:
                    from riva.audit_engine import run_audit

                    result = run_audit(
                        contract.id, agent["cwd"], triggered_by="auto"
                    )

                    # Automation chain on passing audit
                    if result.get("overall_verdict") == "passed":
                        # Run full automation: PR → CI poll → close issue
                        try:
                            from riva.automation import on_audit_passed

                            on_audit_passed(
                                contract.id,
                                result["audit_id"],
                                agent["cwd"],
                            )
                        except Exception:
                            logger.debug(
                                "Automation chain skipped for %s",
                                contract.id,
                            )

                        # Propose scene completion
                        try:
                            from riva.play_write import propose_scene_update

                            proposal = propose_scene_update(
                                contract.id, result["audit_id"]
                            )
                            if proposal:
                                logger.info(
                                    "Scene update proposed for contract %s: "
                                    "%d candidate scenes",
                                    contract.id,
                                    len(proposal.get("scenes", [])),
                                )
                        except Exception:
                            logger.debug(
                                "Scene proposal skipped for %s",
                                contract.id,
                            )
                except Exception:
                    logger.exception(
                        "Auto-audit failed for contract %s", contract.id
                    )

    broker.on_agent_done(_on_agent_done)

    # Phase 6: register play write handler
    from riva.rpc_dispatcher import register_method

    def _handle_scene_confirm(*, scene_id: str = "", **_kw):
        if not scene_id:
            from riva.errors import RivaError

            raise RivaError("scene_id is required")
        from riva.play_write import confirm_scene_complete

        return confirm_scene_complete(scene_id)

    register_method("riva/scene/confirm", _handle_scene_confirm)

    logger.info("Phase 5+6 handlers registered (audit + projects + play write)")


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

    # Share clients with automation module
    from riva.automation import set_devops_clients
    set_devops_clients(forgejo, woodpecker)

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
    provider: object | None = None,
    socket_path: Path | None = None,
    ready_event: asyncio.Event | None = None,
    simulated: bool = False,
    disable_guard: bool = False,
    structured: bool = False,
) -> None:
    """Start the RIVA Unix socket server.

    Args:
        provider: LLM provider for entry guard. None disables the guard.
        socket_path: Override socket path (for testing).
        ready_event: Set when the server is ready to accept connections.
        simulated: Use SimCCManager (no real Claude Code). Safe for testing.
        disable_guard: Skip entry guard entirely (for benchmarking plan quality).
        structured: Use StructuredPlanner instead of PlanEngine.
    """
    guard_provider = None if disable_guard else provider
    if disable_guard:
        logger.info("Entry guard DISABLED (benchmark mode)")
    sock_path = socket_path or get_socket_path()
    sock_path.parent.mkdir(parents=True, exist_ok=True)

    # Remove stale socket
    if sock_path.exists():
        sock_path.unlink()

    # Ensure schema and register handlers
    # provider = full LLM provider (plan engine needs this)
    # guard_provider = None if guard disabled, otherwise same as provider
    ensure_schema()
    set_start_time()
    _register_phase2_handlers(provider, structured=structured)  # Plan engine gets real provider
    manager = _register_phase3_handlers(simulated=simulated)
    broker = _register_phase4_handlers(manager)
    _register_phase5_handlers(manager, broker)
    _register_pm_handlers()
    _register_devops_handlers()

    async def client_handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await _handle_client(reader, writer, provider=guard_provider)  # Guard uses guard_provider

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
    parser.add_argument(
        "--simulated", action="store_true",
        help="Use simulated CCManager (no real Claude Code). Safe for testing.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.simulated:
        logger.info("SIMULATED MODE — no real Claude Code will be spawned")

    # Try to get an LLM provider for entry guard
    provider = None
    try:
        from trcore.db import get_db
        from trcore.providers import get_provider

        db = get_db()
        provider = get_provider(db)
        logger.info("Entry guard enabled (LLM provider available)")
    except Exception as exc:
        logger.warning("Entry guard disabled (no LLM provider): %s", exc)

    # Handle SIGTERM gracefully
    loop = asyncio.new_event_loop()

    def _shutdown(sig: signal.Signals) -> None:
        logger.info("Received %s, shutting down...", sig.name)
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown, sig)

    try:
        loop.run_until_complete(
            start_server(provider=provider, simulated=args.simulated)
        )
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
