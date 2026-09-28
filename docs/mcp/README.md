# Arkennemasis MCP

Connect an MCP-capable AI client to your local ComfyUI. The gateway supports workflow and canvas editing, execution, selected custom-node development, maintenance, and model/media inspection. ComfyUI runs the jobs on your computer. The gateway itself needs no model-provider login; AI-client limits and credentials or charges for paid nodes still apply.

Start with the [installation and daily-use guide](../../setup_guides/01_arkennemasis_mcp_guide.md). For details see [architecture](architecture.md), [tool reference](tools.md), [OAuth](oauth.md), and [testing and release checks](development.md).

The [0.2.0 verification record](release-verification.md) separates completed local checks from remaining provider and live-restart checks. See the [agent handoff](handoff.md) for the current continuation scope.

## What a fresh clone does

Cloning the pack into `ComfyUI/custom_nodes/comfyui-arkennemasis` makes its local bridge and **MCP setup** button available after ComfyUI restarts. It does not silently install optional gateway dependencies, sign into Tailscale, start a public tunnel, or share a browser canvas. The local setup screen handles installation-specific choices and creates one fast-FP16 with-MCP launcher for supported Windows portable installations.

After **3. Create launchers**, use `run_nvidia_gpu_fast_fp16_accumulation.bat` for ordinary fast-FP16 ComfyUI or `run_nvidia_gpu_fast_fp16_accumulation_with_mcp.bat` for the same mode with the gateway and foreground Funnel connection. No MCP companions are created for the standard GPU or CPU BATs. Close the active launcher before switching. The ordinary BAT starts no gateway or Funnel, but does not stop a separately started manual connection. Local setup/sharing controls remain available as part of the node pack.

Closing the with-MCP BAT ends the connection it owns; the Tailscale system service is unchanged. Saved MCP launch overrides and managed remote restart require that with-MCP launcher. A stable URL is reused across ordinary restarts, so an existing web connector can reconnect without being recreated. Keep the computer awake and connected while using it.

## Access controls

The owner selects installation permissions in **MCP setup**. `comfy:develop` and `comfy:maintain` are off by default. Development also requires an explicit list of allowed custom-node folder names, including any new pack the AI may create. Changing permissions requires restarting the gateway.

| Permission | Allows |
| --- | --- |
| `comfy:read` | Workflow/node discovery, shared-canvas reads, queue/progress and connection status |
| `comfy:write` | Saved workflow and shared-canvas edits with revision checks |
| `comfy:run` | Explicit generation, targeted interruption and removal of selected pending jobs |
| `comfy:media` | Supported input/output/temp/model inventory, metadata, previews and file organization |
| `comfy:develop` | Selected node source, Git/package inspection, diagnostics and explicit selected-file node tests |
| `comfy:maintain` | Defined repository changes, reviewed package installation, model downloads, launch settings and controlled restart |

The private connection URL grants the installation permissions currently enabled. In OAuth mode, token scopes further restrict them. A tool being listed does not mean its capability has been enabled for this installation.

Ordinary source tools exclude credentials, private catalogues and the MCP's own access-control files. Paths outside selected roots, traversal, links, junctions and hard links are rejected. Model directories outside ComfyUI require explicit owner configuration in `model_roots`; discovering an external directory does not grant access to it automatically.

**These controls constrain MCP operations; they are not an operating-system sandbox.** Editing Python custom nodes, running their tests, or installing dependencies allows code to execute with ComfyUI's computer permissions. Use a separate OS account, container or machine if you need that stronger boundary.

## Connection reliability

The badge and `get_connection_status` distinguish the gateway, tunnel, ComfyUI backend and shared browser session. Local readiness does not prove an AI provider can reach the public HTTPS endpoint.

The companion tolerates temporary failures and retries a failed gateway or tunnel independently. It keeps the gateway available for diagnostics when the backend stops responding. Recovery is bounded; repeated failures produce an error instead of an endless restart loop. Connection transitions are saved in `.local/connection-events.jsonl`.

Canvas sessions stay registered while their ComfyUI WebSocket is connected. Brief disconnections have a reconnect grace period. Closing or refreshing a tab ends sharing, and **Stop sharing** explicitly revokes it. A session can also expire after a prolonged disconnect. The AI must re-read the selected canvas and revision before a new edit; it must never silently switch to another tab.

Long operations return an operation ID for polling. For a retry, reuse the same canonical UUID `request_id` with the same arguments. Persisted records prevent blind replay of uncertain generation or maintenance operations. A lost response is not proof that nothing happened: inspect the operation/job and current state before requesting a new action.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| AI says the MCP tool is unavailable | Ensure the connector is enabled in that conversation; refresh its tool definitions after a server update. This can be a client configuration issue. |
| Network connection failed | Check the badge and `python mcp_service/launch.py doctor`, then test the public route. A healthy local gateway can still have a tunnel/DNS/TLS failure. |
| `no_canvas` or no shared sessions | Open the intended ComfyUI tab and click **Share canvas**. Keep it open. Do not use an old session ID after a refresh. |
| Revision conflict | Read the current graph/file again, compare changes, and prepare a new edit. Do not overwrite another edit blindly. |
| Operation is unconfirmed | Inspect the recorded operation and its actual target. It is deliberately not replayed automatically after uncertainty. |
| Development/maintenance denied | Enable the appropriate permission in local setup and select the node pack, then restart the gateway. |
| Restart failed | Read recent ComfyUI/import logs; the gateway can remain available while its BAT owner is alive. A stopped backend may require relaunching the BAT after fixing code. |
| Previews unavailable | Check that the running node emits previews. File previews need FFmpeg on the gateway's PATH; metadata support varies by format. |

## Practical limits

Windows portable BAT lifecycle is the automatic setup target. Other installations can use the manual gateway and their own tunnel/process management. Not every ComfyUI launcher supports remote restart; unsupported launchers return an explicit error. Universal hot reload, automatic core/frontend upgrades, advanced profiling, and partial-graph execution are not implemented.

The MCP returns bounded JSON, images and frame previews. It is not a bulk video streaming service. Model downloads go directly to your computer from configured hosts, with checksum and size checks; model binaries are not sent through the AI conversation. Large local copies and hashing may take time.

Funnel has provider-controlled bandwidth limits. Neither unlimited transfer nor uninterrupted public reachability is promised. See the current [Tailscale Funnel documentation](https://tailscale.com/docs/features/tailscale-funnel). AI-client availability, usage limits and media presentation are also outside the gateway's control.
