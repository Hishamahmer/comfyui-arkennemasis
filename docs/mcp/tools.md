# MCP tool reference

Permissions are installation-level opt-ins, with OAuth token scopes applied additionally when configured. See [access controls](README.md#access-controls). Tools can be listed even when their capability is disabled. Long tasks return a request/operation record: poll `get_operation` using the same `request_id`.

## Workflows and live canvas

| Tools | Behavior |
| --- | --- |
| `server_status`, `list_node_types`, `get_node_schema` | Backend and installed node inspection |
| `list_workflows`, `read_workflow` | Saved documents with format and revision |
| `save_workflow`, `patch_workflow`, `restore_workflow` | Revision-checked saved JSON with backup snapshots |
| `validate_prompt` | Validate executable API prompt JSON without queuing |
| `list_canvases`, `read_canvas` | Select an explicitly shared browser session and read its current graph/revision |
| `apply_canvas`, `patch_canvas`, `undo_canvas` | Revision-checked browser edits with request identity and frontend acknowledgment |

Saved UI workflow JSON and executable API prompt JSON differ. `read_canvas` obtains both through ComfyUI's frontend. An edit marks the canvas modified but does not silently save it to disk or run it. Read the current revision before preparing a change; stale revisions fail rather than overwrite concurrent edits.

## Execution and diagnostics

| Tools | Behavior |
| --- | --- |
| `queue_prompt`, `run_live_canvas` | Explicitly queue one API prompt or exact shared-canvas revision; the latter uses the browser client identity for highlights |
| `get_job`, `get_queue` | Read actual job status or current/pending job IDs |
| `cancel_pending_job`, `remove_pending_jobs` | Remove only specified pending jobs, preserving concurrent additions |
| `interrupt_job` | Request interruption of the specified running job, then poll to confirm |
| `get_execution_progress`, `get_execution_preview` | Poll bounded recent progress and a small preview for an exact job when emitted |
| `get_connection_status`, `check_public_connection` | Separate local layers and explicitly probe the configured public HTTPS route |
| `read_comfyui_log`, `inspect_comfyui_runtime` | Read bounded diagnostic logs or the backend's actual interpreter/process information |
| `restart_comfyui`, `get_restart_status` | Request and inspect one restart of a supported registered session; queue must be idle |
| `release_model_memory` | Request model/cache release while idle; acknowledgment does not claim a measured VRAM reduction |
| `get_launch_settings`, `change_launch_settings`, `restore_launch_settings` | Inspect/save/restore supported launch overrides, applied on explicit managed restart or `*_with_mcp.bat` launch; ordinary BAT arguments are unchanged |
| `get_operation` | Inspect a long task; requires read permission and that operation's own scope |

Use one canonical UUID per intended generation or background operation. Retry that UUID only with identical arguments. If a response or process is lost, inspect its record and actual state before creating another UUID. Request acceptance, backend readiness and a completed generation are different results.

Launch settings are whitelisted against the installed ComfyUI CLI. Changes do not restart automatically. There is no arbitrary-process kill/start tool or universal hot reload. Progress and previews are requested snapshots; MCP does not make an AI client continuously watch a generation by itself.

## Custom-node development and maintenance

| Tools | Behavior |
| --- | --- |
| `list_node_files`, `read_node_source`, `search_node_source` | Bounded source access in owner-enabled packs |
| `edit_node_source` | Write, exact-text patch, rename, recoverably delete or restore a selected source path |
| `node_source_history` | Inspect available source revisions/backups |
| `validate_node_source` | Static validation for supported source formats; does not import Python nodes |
| `create_node_pack` | Scaffold a pack in an explicitly allowed new folder |
| `run_node_tests` | Execute one selected `test_*.py` unittest file with ComfyUI's Python, bounded runtime/output; no remote command argument |
| `inspect_node_repository` | Git status, history or selected-file diff |
| `change_node_repository` | Clone a public GitHub HTTPS repository, clean fast-forward update, create a branch without switching, or commit selected source paths; never push |
| `inspect_python_packages` | Installed package inventory and dependency conflicts in ComfyUI's Python |
| `plan_python_packages` | Resolve exact `name==version` requests into a reviewable wheel-only plan |
| `install_planned_packages` | Apply a valid plan after environment/conflict checks and any required local owner approval |

Source paths begin with the selected pack name. Credentials, private catalogue content and the MCP's own enforcement code are protected from ordinary source tools. Git changes stay within enabled packs; protected MCP repositories cannot be broadly updated through these tools. Hooks, submodules, unrestricted checkout/revert, SSH credentials and push are not exposed.

Package plans pin wheel hashes, reject introduced dependency conflicts and recheck the environment before installation. Protected dependencies such as Torch/NumPy need approval of the exact plan in the local setup screen. There is no general `pip` argument or package-uninstall tool, source-build support, or automatic environment rollback.

**Node tests and dependencies execute code with ComfyUI's OS permissions.** Source-folder restrictions do not contain what that code can do once executed. Static checks and unit tests also do not replace an intentional real workflow validation/run.

## Models, media and output

| Tools | Behavior |
| --- | --- |
| `list_outputs`, `get_output_image`, `upload_reference_image` | Job output descriptors, bounded inline image retrieval and reference-image import |
| `list_asset_files`, `inspect_asset_file`, `read_asset_text` | Scoped input/output/temp/model inventory and bounded metadata/text inspection |
| `hash_asset_file` | Background SHA-256 calculation, including large model files |
| `preview_asset_file` | Small image/video frame preview using optional local FFmpeg |
| `organize_asset_file`, `list_deleted_assets` | Copy/move, recoverably delete and restore without overwriting destination files |
| `download_model_file` | Checked HTTPS model download to a configured model category, with required SHA-256, byte/disk limits and redirect host checks |

Model categories are returned by `list_asset_files`; do not guess arbitrary absolute paths. Owner-configured external model roots are explicit exceptions. Model metadata reads do not load model tensors. Media support varies by format and locally available FFmpeg/ffprobe/Pillow; full audio/video playback in an AI chat is client-dependent.

Downloads use the owner's approved host list on each redirect, public-address checks, TLS validation and partial-file cleanup. They do not overwrite existing models. The default supported provider hosts do not guarantee that every provider/CDN link is accepted; a new host needs explicit owner configuration. Model binaries stay on the ComfyUI machine.

## Deliberately outside this release

General terminal execution, arbitrary ComfyUI API forwarding, blanket filesystem access, automatic ComfyUI core/frontend upgrades, universal hot reload, advanced per-node profiling, partial-graph test execution and bulk media streaming are not exposed. Git/package/model actions are defined operations, not a complete ComfyUI-Manager replacement.
