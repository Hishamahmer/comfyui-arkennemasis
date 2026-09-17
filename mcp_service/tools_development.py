"""Custom-node source tools, separate from workflow editing and installation."""

from pathlib import Path
from typing import Literal

from .development import NodeWorkspace


def register_development_tools(tool, settings):
    def workspace():
        return NodeWorkspace(Path(settings.comfy_root) / "custom_nodes", settings.state_dir, settings.allowed_node_packs)

    @tool("comfy:develop")
    def list_node_files(pack: str | None = None, directory: str = "", limit: int = 200) -> dict:
        """List editable source files in owner-enabled custom-node packs. Hidden files, secrets and MCP access-control code are excluded."""
        return workspace().list(pack, directory, limit)

    @tool("comfy:develop")
    def read_node_source(path: str, start_line: int = 1, max_lines: int = 300) -> dict:
        """Read a node source file and its full-file revision. Paths start with the enabled pack folder, e.g. my_nodes/nodes.py."""
        return workspace().read(path, start_line, max_lines)

    @tool("comfy:develop")
    def search_node_source(query: str, pack: str | None = None, limit: int = 50) -> dict:
        """Find literal text in enabled node source files, with bounded matching lines."""
        return workspace().search(query, pack, limit)

    @tool("comfy:develop", write=True)
    def edit_node_source(path: str, action: Literal["write", "patch", "rename", "delete", "restore"],
                         expected_revision: str | None = None, content: str = "", old_text: str = "",
                         new_text: str = "", new_path: str = "", backup_id: str = "") -> dict:
        """Create/write, replace exact text, rename, recoverably delete or restore node source. Read first and supply expected_revision for existing files. Changes return a diff and backup; they do not restart or run ComfyUI."""
        source = workspace()
        if action == "write":
            return source.write(path, content, expected_revision)
        if action == "patch":
            return source.patch(path, old_text, new_text, expected_revision)
        if action == "rename":
            return source.rename(path, new_path, expected_revision)
        if action == "delete":
            return source.delete(path, expected_revision)
        return source.restore(path, backup_id, expected_revision)

    @tool("comfy:develop")
    def node_source_history(path: str, limit: int = 20) -> dict:
        """List restorable source revisions for one enabled node file."""
        return workspace().history(path, limit)

    @tool("comfy:develop")
    def validate_node_source(path: str) -> dict:
        """Check Python syntax/node declarations or JSON/TOML structure without importing or running code. Runtime validation still requires restart and a test workflow."""
        return workspace().validate(path)

    @tool("comfy:develop", write=True)
    def create_node_pack(pack: str, class_name: str, display_name: str = "") -> dict:
        """Create a minimal node package in an absent owner-enabled folder. Existing packs are never overwritten; edit its source next and verify registration after restart."""
        return workspace().scaffold(pack, class_name, display_name)
