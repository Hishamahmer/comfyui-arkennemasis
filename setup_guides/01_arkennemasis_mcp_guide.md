# Arkennemasis MCP — Gateway & Canvas Bridge Guide

Control your local ComfyUI instance directly from Web AI clients (ChatGPT Web, Claude Web) via secure Tailscale Funnel, with optional local desktop client support.

---

> [!TIP]
> ### 💡 The Killer Feature: Unlimited ComfyUI Control via ChatGPT Web
> Developer tools like Codex CLI and API plans have **separate, restrictive usage limits** that can max out quickly during heavy workflow iteration.
> In contrast, **ChatGPT Web (`chatgpt.com`)** offers expansive, virtually unlimited interaction on standard Plus, Team, and Pro subscriptions.
> By connecting Arkennemasis MCP's secure Tailscale Funnel endpoint to **ChatGPT Web**, you can create, control, edit, rewire, and trigger your local ComfyUI workflows directly through normal browser conversations — **without worrying about hitting Codex CLI rate caps or burning developer credits**.

---

## 1. What Makes Arkennemasis MCP Different?

Standard ComfyUI MCP implementations are usually simple HTTP scripts that perform blind JSON file editing on disk. Arkennemasis MCP is fundamentally different:

| Feature | Standard ComfyUI MCP | Arkennemasis MCP |
| :--- | :--- | :--- |
| **Web Interface AI Access** | Requires manual port forwarding or risky public exposure. | **Built-in Tailscale Funnel.** Secure HTTPS bridge enabling **ChatGPT Web (`chatgpt.com`)** access with zero router configuration. |
| **Usage Limits & Quotas** | Stuck with Codex CLI / API tokens with strict hourly/weekly caps. | **Bypass CLI Caps with ChatGPT Web.** Chat, build, and debug workflows in the web UI using your regular ChatGPT subscription. |
| **Canvas Editing** | Blind disk edits. You must reload or refresh the page to see changes. | **Live Browser Canvas Bridge.** Directly manipulates nodes and links in your open browser tab in real time with visual feedback and frontend undo. |
| **Startup Lifecycle** | Requires manual terminal commands every time. | **Tied to ComfyUI BAT.** Starts automatically when you run `run_nvidia_gpu.bat` and stops when the BAT window is closed. |
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

### A. Primary Purpose: Web AI Clients via Tailscale Funnel (ChatGPT Web / Claude Web) [RECOMMENDED]

**This is the primary purpose and core architecture of Arkennemasis MCP.** It connects the web version of ChatGPT (`chatgpt.com`) directly to your local ComfyUI instance without manual port forwarding, dynamic DNS, or firewall hassle — letting you build, edit, and control workflows with virtually unlimited usage while completely avoiding restrictive Codex CLI rate limits.

1. **Verify Connection:**
   * Ensure ComfyUI is running and the bottom-left badge shows **Connection: online**.
2. **Retrieve Your Private Funnel URL:**
   * Open `mcp_service/.local/connection.txt` to find your persistent, authenticated HTTPS Tailscale Funnel endpoint:
     ```
     https://hisham.tailb871a6.ts.net/mcp/s/<your-private-token>
     ```
3. **Connect in ChatGPT Web / Claude Web:**
   * In your web AI client's MCP connector or Actions setup, add a new server using your Funnel URL.
   * **Authentication:** Select **No Auth** (your private URL contains an embedded cryptographic token that authenticates your session securely).
4. **Full Web Canvas Control Without Usage Limits:**
   * **Inspect & Navigate:** Ask ChatGPT Web to examine the current workflow on your open ComfyUI tab.
   * **Build & Rewire:** Ask ChatGPT Web to add nodes, rewire links, change parameters, and configure settings directly on your live screen.
   * **Execute & Monitor:** Queue prompts, check render status, and review errors right in the web conversation.
   * **Zero Limit Anxiety:** Because you are on the web interface with your standard subscription, you don't burn Codex CLI quotas or pay per-token developer API bills.

---

### B. Secondary / Local Method: Local Desktop Clients via Stdio (Codex CLI / Claude Desktop)

For local terminal development or desktop agent tools running on the same host machine:

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
