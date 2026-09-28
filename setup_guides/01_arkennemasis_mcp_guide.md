# Arkennemasis MCP — setup and daily use

Use your web AI client to inspect, edit and run local ComfyUI workflows, and optionally develop selected custom nodes. Your models and GPU stay local; requested workflow data and results travel to the AI client. Its subscription limits and any paid nodes' own charges still apply.

## 1. Install the pack

Clone this repository into ComfyUI's `custom_nodes` directory:

```sh
git clone https://github.com/Hishamahmer/comfyui-arkennemasis.git
```

Follow the [pack installation instructions](../README.md) for its normal node dependencies, then restart ComfyUI. The optional MCP gateway has its own setup below. It is not required to use the pack's other nodes.

You will need:

- A working local ComfyUI installation.
- CPython 3.11 or newer with pip for the optional gateway. A separate system Python or virtual environment is suitable; setup shows the interpreter it will use.
- For automatic web access, Tailscale installed and signed in, with Funnel enabled for this machine. Follow the official [Funnel setup](https://tailscale.com/docs/features/tailscale-funnel) if its first-time permission prompt appears.
- An AI client/account that supports custom MCP connections.

There is a one-time owner setup. A Git clone cannot sign into your tunnel account, grant its public access permission or create a connector in your AI account automatically.

## 2. Use the local setup screen

Open ComfyUI on the same computer using its localhost or `127.0.0.1` address. Click **MCP setup** at the bottom right. Setup is available only from the local ComfyUI page; it is not exposed through the public MCP endpoint.

1. Check the detected **ComfyUI Python** and **Python executable for the optional MCP gateway**. They may be different. Package maintenance targets ComfyUI's Python; gateway dependencies go into the pack's private `mcp_service/.runtime/pyXY` directory.
2. Check the **Fixed public HTTPS address** detected from Tailscale. It looks like `https://YOUR-COMPUTER.YOUR-TAILNET.ts.net`. This field takes the origin only, not `/connect/.../mcp` and not a localhost address.
3. Select permissions. To let the AI edit code, enable **Create and edit selected custom-node code** and list exact allowed folder names, one per line. Include a new folder name if the AI should create it. Enable maintenance separately for package/repository changes or restart.
4. Click **1. Save settings**, then **2. Install MCP dependencies**. Wait for completion.
5. On Windows portable, click **3. Create launchers**. Setup creates only `run_nvidia_gpu_fast_fp16_accumulation_with_mcp.bat`, preserving the fast-FP16 flags. Original BAT files stay unchanged for ordinary ComfyUI use; setup edits one only to remove an older Arkennemasis auto-start hook, keeping a private backup. Unrecognized/custom companion files are not overwritten.
6. Save your work, close ComfyUI's existing BAT and start `run_nvidia_gpu_fast_fp16_accumulation_with_mcp.bat`. If the current session already uses a supported with-MCP launcher, **4. Start connection** can request startup directly. The badge reports readiness.
7. Click **Show / copy connection URL**. Use the entire returned URL in your AI connector, with the authentication method shown alongside it.

Settings changes apply after restarting the MCP connection. Saving settings preserves existing credentials and the private URL. If an isolated dependency directory is damaged, use the [runtime repair instructions](../docs/mcp/development.md#repairing-the-optional-runtime).

Launcher creation requires Windows portable's recognized `run_nvidia_gpu_fast_fp16_accumulation.bat`. Its MCP companion is created in the same portable folder. The standard GPU and CPU BATs receive no MCP companions and are not changed unless they still contain a legacy Arkennemasis auto-start hook, which setup removes. For other platforms or launchers, use [manual setup](#6-manual-or-local-client-setup).

## 3. Connect the web AI client

Private-link mode produces a URL shaped like:

```text
https://YOUR-COMPUTER.YOUR-TAILNET.ts.net/connect/PRIVATE-SECRET/mcp
```

This is a shape example, not a working address. Get your actual complete URL from **Show / copy connection URL** or `mcp_service/.local/connection.txt`. The root hostname alone is not the MCP endpoint.

For a private connection link:

1. Open your AI client's custom MCP connector/plugin form.
2. Choose **Server URL**, paste the complete URL, and choose **No Auth**.
3. Create the connector and enable it in the conversation where you will use it.
4. Ask it to call `get_connection_status` before making changes.

**No Auth** means the client does not add a separate authorization header. The URL contains the access secret. Anyone with that complete URL can use the installation's enabled permissions; keep it out of shared screenshots, public logs and GitHub. OAuth is a separate configuration described in the [OAuth guide](../docs/mcp/oauth.md).

Use the current client documentation for account availability and UI details: [ChatGPT connection guide](https://developers.openai.com/plugins/deploy/connect-chatgpt), [ChatGPT developer mode](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt). For Claude, use its custom remote MCP connector settings and the same complete endpoint. A Custom GPT Action uses a different API contract and is not a substitute for adding an MCP connector.

After a server update adds tools, refresh/review the connector's tool definitions using the client's current controls. A stable URL avoids replacing the address; it does not force the AI client to refresh cached tools.

## 4. Daily use and canvas sharing

Choose the launcher for this session:

| Use | Fast-FP16 launcher | What starts |
| --- | --- | --- |
| ComfyUI without the AI connection | `run_nvidia_gpu_fast_fp16_accumulation.bat` | ComfyUI directly; no automatic MCP gateway or Funnel |
| ComfyUI with the AI connection | `run_nvidia_gpu_fast_fp16_accumulation_with_mcp.bat` | ComfyUI with its restart supervisor, MCP gateway and foreground Funnel |

Only fast-FP16 has a with-MCP companion. Standard GPU and CPU BATs remain ordinary launchers. Close the active launcher before switching; do not run both against the same ComfyUI instance. Closing the with-MCP BAT stops the gateway and Funnel it owns. The Tailscale system service is unchanged.

The ordinary BAT does not stop a gateway/tunnel that you started separately. Stop that manual connection separately if it is still running. The pack's local **MCP setup** and canvas-sharing controls remain visible during ordinary ComfyUI use; their presence does not mean a public connection has started. MCP launch-setting overrides and managed remote restart apply only when ComfyUI uses the with-MCP launcher.

The bottom-left badge reports **Connection: online**, **starting**, or a failure state. Hover over it for details. This reports local readiness; a public-network check is separate. Temporary failures trigger bounded recovery, with diagnostics if recovery cannot finish. A stopped ComfyUI backend does not immediately tear down the gateway, so the AI may still read diagnostics while the BAT remains open.

Click **Share canvas** in the specific ComfyUI tab you want the AI to use. Other tabs remain private. Keep that tab open. **Stop sharing**, closing the tab or a page refresh ends sharing. A shared tab follows its active workflow, so ask the AI to identify the selected workflow before editing it.

Start with a read-only request:

> Use Arkennemasis MCP. Check the connection, list shared canvases, and explain the workflow in the selected tab. Do not change or run anything yet.

Then make a small edit on a duplicate workflow:

> Change the positive prompt to [your prompt], preserving the other settings and connections. Apply it to this shared canvas, validate it, and show the change. Do not run it.

For generation:

> Run that exact canvas revision once. Track the returned job and show its output when complete.

Saving a workflow file and editing the open canvas are separate operations. Canvas edits mark it modified; save it in ComfyUI when you want to retain them. The AI should reuse an operation's request ID if a response is lost and inspect the result before requesting a new run.

The hostname normally remains the same across Funnel sessions; see [Tailscale's stable-name example](https://tailscale.com/docs/reference/examples/funnel). Changing the machine/tailnet identity or rotating the private secret changes the complete URL. Being offline makes it unreachable without generating a replacement URL.

## 5. Develop custom nodes

Once development and the target folder are enabled in local setup, ask the AI:

> Inspect the source in [allowed folder]. Explain the change first, then edit the selected node, keep a backup, and validate its Python syntax.

Source edits use revisions and backups. The AI can inspect history, restore a previous version, scaffold a pack and run a specifically selected unittest file. Static validation does not execute a node or prove it will generate correctly. Node tests execute code with ComfyUI's OS permissions; the allowed-folder rule is not an OS sandbox.

Dependency changes use an exact-version plan. Protected changes, such as Torch or NumPy, appear under **Review critical package changes** in local MCP setup. Review the listed versions there before allowing that exact plan. The AI cannot approve this owner action through its public tool.

An explicit restart requires an idle queue and a supported session started through the with-MCP launcher. Readiness and logs confirm the outcome. The ordinary BAT uses ComfyUI's original arguments and does not apply saved MCP launch overrides. The MCP does not promise universal Python hot reload or automatic rollback of package installations. See the [tool reference](../docs/mcp/tools.md) for precise operations and limits.

## 6. Manual or local-client setup

Run these from this repository's directory, using the Python selected for the gateway:

```sh
python -m pip install --target mcp_service/.runtime -r mcp_service/requirements.txt
python mcp_service/launch.py init
python mcp_service/launch.py doctor
```

For a trusted local client, configure it to launch:

```text
YOUR-PYTHON /ABSOLUTE/PATH/comfyui-arkennemasis/mcp_service/launch.py serve --transport stdio
```

The client owns that process. Keep ComfyUI running separately. A generic JSON configuration is:

```json
{
  "mcpServers": {
    "arkennemasis": {
      "command": "/absolute/path/to/python",
      "args": [
        "/absolute/path/to/comfyui-arkennemasis/mcp_service/launch.py",
        "serve", "--transport", "stdio"
      ]
    }
  }
}
```

On Windows, use your full Windows paths, escaping each backslash as `\\` inside JSON strings. Do not copy another user's executable path unchanged.

For manually managed web access, save a fixed origin and forward only the gateway's loopback port, normally 8190:

```sh
python mcp_service/launch.py configure-web --public-url https://YOUR-COMPUTER.YOUR-TAILNET.ts.net
python mcp_service/launch.py web
```

In another terminal, start the foreground tunnel after inspecting existing Tailscale mappings:

```sh
tailscale funnel status --json
tailscale funnel --https=443 http://127.0.0.1:8190
```

Keep both terminals open. Stop them with Ctrl+C. Do not use this manual path alongside a BAT companion already owning the same gateway/Funnel. Never forward ComfyUI's full 8188 interface as a replacement for the MCP gateway.

Without a fixed public origin, the optional `web` fallback can use an installed `cloudflared` executable to create a temporary Quick Tunnel. This changes the hostname; use a fixed origin for a reusable connector.

## 7. Check a problem

```sh
python mcp_service/launch.py doctor
```

This checks local configuration, backend and bridge. See [troubleshooting](../docs/mcp/README.md#troubleshooting) for network failure, missing canvas, stale revision and failed-restart cases, and [development checks](../docs/mcp/development.md) for explicit public and generation tests.

Funnel has bandwidth limits. The gateway bounds results and previews, and AI clients impose their own limits. It is designed for workflow control and requested results, not unlimited bulk media transfer.
