# Arkennemasis MCP — Gateway & Canvas Bridge Guide

Control your local ComfyUI instance from external AI clients (Claude Desktop, Codex CLI, ChatGPT Web, Claude Web, Cursor).

---

## 1. What Makes Arkennemasis MCP Different?

Standard ComfyUI MCP implementations are usually simple HTTP scripts that perform blind JSON file editing on disk. Arkennemasis MCP is fundamentally different:

| Feature | Standard ComfyUI MCP | Arkennemasis MCP |
| :--- | :--- | :--- |
| **Canvas Editing** | Blind disk edits. You must reload or refresh the page to see changes. | **Live Browser Canvas Bridge.** Directly manipulates nodes and links in your open browser tab in real time with visual feedback and frontend undo. |
| **Startup Lifecycle** | Requires manual terminal commands every time. | **Tied to ComfyUI BAT.** Starts automatically when you run `run_nvidia_gpu.bat` and stops when the BAT window is closed. |
| **Web AI Access** | Requires manual port forwarding or risky public exposure. | **Built-in Tailscale Funnel.** Provides a persistent, authenticated external HTTPS URL without exposing raw ComfyUI port 8188. |
| **Safety & Sandboxing** | Open or arbitrary execution risks. | **Scoped & Opt-In.** Canvas sharing is disabled by default per tab. No arbitrary OS shell access; private tokens and credentials are masked. |
| **Tools & Diagnostics** | Basic queue-only functionality. | **20+ Engineering Tools.** Source code search/patching, Git inspection/commits, model inventory, log inspection, and graceful session restarts. |
| **Process Isolation** | Can conflict with ComfyUI Python packages. | **Isolated Runtime.** Runs its own dependencies (`.runtime/`) under system Python, leaving ComfyUI's torch and portable Python pristine. |

---

## 2. Starting the MCP Service

On this machine, **you do not need to run manual startup commands.**

1. Launch ComfyUI using your standard BAT launcher:
   * `run_nvidia_gpu_fast_fp16_accumulation.bat` (or `run_nvidia_gpu.bat`)
2. Keep the BAT window open while working.
3. The companion automatically waits for ComfyUI to come online, launches the gateway, and opens the foreground Tailscale Funnel.
4. When you close the BAT window, the companion automatically terminates the gateway and closes external forwarding.

---

## 3. The ComfyUI Web Interface Badge

When you open ComfyUI (`http://127.0.0.1:8188`), check the **bottom-left corner** of your browser tab:

* **Connection: online** — The gateway and Tailscale Funnel are ready.
* **Connection: starting** — The gateway is initializing.
* **Connection: offline / error** — The gateway or ComfyUI backend is not reachable. Hover over the badge for diagnostic messages.

### Sharing Your Canvas with AI:
1. Click **Share canvas** in the bottom-left badge.
2. The AI client can now inspect, add, wire, and modify nodes on your active screen in real time.
3. To revoke access, click **Stop sharing** or simply refresh the browser tab (sharing resets to off upon page refresh).

---

## 4. Connecting AI Clients

### A. Local Clients via Stdio (Recommended for Codex & Claude Desktop)

#### **Claude Desktop Configuration (`claude_desktop_config.json`)**:
```json
{
  "mcpServers": {
    "arkennemasis": {
      "command": "C:\\Users\\hisha\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
      "args": [
        "E:\\ComfyUI_windows_portable_nvidia\\ComfyUI_windows_portable\\ComfyUI\\custom_nodes\\comfyui-arkennemasis\\mcp_service\\launch.py",
        "serve", "--transport", "stdio"
      ]
    }
  }
}
```

#### **Codex CLI Configuration (`~/.codex/config.toml`)**:
```toml
[mcp_servers.arkennemasis]
command = 'C:\Users\hisha\AppData\Local\Programs\Python\Python312\python.exe'
args = ['E:\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable\ComfyUI\custom_nodes\comfyui-arkennemasis\mcp_service\launch.py', 'serve', '--transport', 'stdio']
startup_timeout_sec = 30
tool_timeout_sec = 60
```

### B. Web AI Clients via Tailscale Funnel (ChatGPT Web / Claude Web)

1. Ensure the BAT is running and the ComfyUI badge shows **Connection: online**.
2. Open `mcp_service/.local/connection.txt` to find your complete fixed secret URL:
   ```
   https://hisham.tailb871a6.ts.net/mcp/s/<your-private-token>
   ```
3. In your web AI client's MCP connector:
   * **Server URL:** Paste the full URL from `connection.txt`.
   * **Authentication:** Select **No Auth** (possession of the private URL token authenticates the connection).

---

## 5. Diagnostic & Health Verification

To verify the entire MCP pipeline without generating an image:
```powershell
python mcp_service/launch.py doctor
```
This tests:
* Local gateway configuration & loopback binding (`http://127.0.0.1:8190/health`)
* ComfyUI local API responsiveness
* Canvas bridge availability
* Connected shared browser tabs
