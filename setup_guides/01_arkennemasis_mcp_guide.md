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

### For Existing Installation:
ComfyUI's BAT launcher on this machine is already configured.
1. Launch ComfyUI using `run_nvidia_gpu_fast_fp16_accumulation.bat` (or `run_nvidia_gpu.bat`).
2. The companion starts automatically in the background, spins up the gateway, and forwards via Tailscale Funnel.
3. Closing the BAT window terminates the gateway and cleans up the tunnel.

### For Brand New Users / New Installations:
If you just cloned this repository into your `custom_nodes/`:
1. **Option A: Automatic with ComfyUI BAT (Recommended)**
   Add this single line to the top of your `run_nvidia_gpu.bat` (right before `.\python_embeded\python.exe -s ComfyUI\main.py`):
   ```bat
   if exist "ComfyUI\custom_nodes\comfyui-arkennemasis\mcp_service\companion.py" .\python_embeded\python.exe -s "ComfyUI\custom_nodes\comfyui-arkennemasis\mcp_service\companion.py" start
   ```
2. **Option B: Manual Terminal Launch**
   Run the gateway web process directly in PowerShell:
   ```powershell
   python mcp_service/launch.py web
   ```
   *(If you have Tailscale, run `python mcp_service/launch.py configure-web --public-url https://<your-node>.ts.net` once first; if you don't have Tailscale, running `web` automatically provisions a free temporary Cloudflare Quick Tunnel).*

---

## 3. The ComfyUI Web Interface Badge

When you open ComfyUI (`http://127.0.0.1:8188`), check the **bottom-left corner** of your browser tab:

* **Connection: online** — The gateway and tunnel are ready.
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

#### Step 1: Where Do You Find Your Secret URL & Token?
You do **not** need to generate a token manually. The gateway automatically creates a random, cryptographically secure 48-character token on first run and writes your complete, ready-to-use URL to disk:
* Open the generated file:
  ```
  mcp_service/.local/connection.txt
  ```
* Inside, you will find your complete private connection URL formatted like:
  ```
  https://<your-machine-name>.<tailnet>.ts.net/connect/<auto-generated-token>/mcp
  ```
  *(Or `https://<random>.trycloudflare.com/connect/<token>/mcp` if using Cloudflare Quick Tunnel).*

#### Step 2: Connect in ChatGPT Web / Claude Web:
1. Ensure ComfyUI is running and the bottom-left badge shows **Connection: online**.
2. Copy the full private URL from `connection.txt`.
3. In ChatGPT Web / Claude Web:
   * Add a custom MCP tool connector or Custom GPT Action pointing to your private URL.
   * **Authentication:** Select **No Auth** (the embedded URL token authenticates the connection securely).

#### Step 3: Full Web Canvas Control Without Usage Limits:
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
