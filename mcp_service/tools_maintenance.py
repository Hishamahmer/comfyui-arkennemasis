"""Explicit repository and dependency tools; no remote shell or install flags."""

import json
from pathlib import Path
import re
from typing import Literal

from .development import NodeWorkspace
from .maintenance import Maintenance


def register_maintenance_tools(tool, settings, operations):
    def maintenance():
        if not settings.comfy_python:
            raise ValueError("Open local MCP setup to confirm the Python environment used by ComfyUI.")
        workspace = NodeWorkspace(Path(settings.comfy_root) / "custom_nodes", settings.state_dir, settings.allowed_node_packs)
        return Maintenance(workspace, settings.comfy_python, settings.state_dir)

    @tool("comfy:develop")
    def inspect_node_repository(pack: str, view: Literal["status", "log", "diff"] = "status", path: str = "", limit: int = 20) -> dict:
        """Inspect Git status, history or a source-file diff in an owner-enabled node pack. Diff paths are relative to that pack."""
        repo = maintenance()
        if view == "status":
            return repo.git_status(pack)
        if view == "log":
            return repo.git_log(pack, limit)
        return repo.git_diff(pack, path)

    @tool("comfy:maintain", write=True, open_world=True)
    def change_node_repository(pack: str, action: Literal["clone", "update", "branch", "commit"], request_id: str,
                               expected_head: str = "", url: str = "", branch: str = "", paths: list[str] | None = None,
                               message: str = "") -> dict:
        """Start one defined Git operation in an enabled pack. Clone accepts public GitHub HTTPS only; update is clean fast-forward only. Branch creates without switching. Commit stages listed source paths only. Poll get_operation; retry using the same UUID. Never pushes."""
        def run():
            repo = maintenance()
            if action == "clone":
                return repo.git_clone(pack, url)
            if action == "update":
                return repo.git_update(pack, expected_head)
            if action == "branch":
                return repo.git_branch(pack, branch, expected_head)
            return repo.git_commit(pack, paths or [], message, expected_head)
        return operations.submit("node_repository", "comfy:maintain", request_id,
                                 {"pack": pack, "action": action, "expected_head": expected_head, "url": url,
                                  "branch": branch, "paths": paths, "message": message}, run)

    @tool("comfy:develop")
    def inspect_python_packages() -> dict:
        """Inspect packages and dependency conflicts in the configured ComfyUI Python environment. Does not install anything."""
        return maintenance().package_inspect()

    @tool("comfy:maintain", open_world=True)
    def plan_python_packages(packages: list[str], request_id: str) -> dict:
        """Resolve exact name==version requests into a reviewable wheel-only plan with hashes and dependency conflicts. Poll get_operation for plan_id. No packages are installed by planning."""
        return operations.submit("plan_packages", "comfy:maintain", request_id, {"packages": packages},
                                 lambda: maintenance().package_plan(packages))

    @tool("comfy:maintain", write=True, open_world=True)
    def install_planned_packages(plan_id: str, request_id: str) -> dict:
        """Install a previously reviewed plan. Changes to Torch, NumPy and other protected packages require the owner to approve this plan in local MCP setup. Rechecks environment/conflicts. Poll get_operation; no automatic rollback is promised."""
        if not isinstance(plan_id, str) or not re.fullmatch(r"[0-9a-f]{32}", plan_id):
            raise ValueError("Use the plan_id returned by package planning.")
        def run():
            approval = Path(settings.state_dir) / "maintenance" / f"approved-{plan_id}.json"
            approved = None
            if approval.is_file() and approval.stat().st_size <= 65536:
                data = json.loads(approval.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("plan_id") == plan_id:
                    approved = data.get("protected_changes")
            return maintenance().package_apply(plan_id, approved)
        return operations.submit("install_packages", "comfy:maintain", request_id, {"plan_id": plan_id}, run)
