# Arkennemasis MCP

Control **your local ComfyUI** from an MCP client. The gateway is independent of
Codex/Claude Code login and uses ComfyUI's installed models and GPU. AI client
usage limits and any paid API nodes' own credentials still apply.

Version 0.1 implements workflow read/create/patch/restore, node discovery,
validation, queue/status, pending-job cancellation, image upload/results and
an opt-in live canvas bridge. It does not install custom nodes, download models,
restart ComfyUI, run arbitrary shell commands or serve large video downloads.

## Start on this computer

This computer uses a fixed Tailscale Funnel hostname,
`https://hisham.tailb871a6.ts.net`. The complete private MCP URL is in
`mcp_service/.local/connection.txt`. **Your usual ComfyUI BAT starts the MCP
connection; closing that BAT window stops it.** There is no MCP connection
running just because you signed into Windows. The previous **Arkennemasis MCP**
sign-in task is disabled, and its persistent Funnel forwarding has been removed.

1. Launch `run_nvidia_gpu_fast_fp16_accumulation.bat` as usual. The standard
   `run_nvidia_gpu.bat` and `run_cpu.bat` also start the connection. The companion
   waits for ComfyUI, then starts the gateway and its Tailscale Funnel. Keep the
   BAT window open while using ComfyUI.
2. In ComfyUI, look at the bottom-left **Arkennemasis MCP** badge. **Connection:
   starting** means it is preparing; **Connection: online** means the local
   gateway and Funnel are ready. **Offline**, **error** or **unavailable** means
   the connector is not ready. Hover over that line for details. After installing
   this update, restart ComfyUI and refresh its page once to load the new badge;
   save any unsaved workflow before refreshing.
3. Copy the complete URL from
   `mcp_service/.local/connection.txt` into your web AI connector's **Server URL**
   field and choose **No Auth**. See [web connection details](#connect-chatgpt-web-or-claude-web-with-no-auth)
   below. Replace the old temporary URL once; this fixed URL survives gateway,
   tunnel and computer restarts.
4. To expose the current canvas to your authorized MCP client, click **Share
   canvas** in the **Arkennemasis MCP** badge at the bottom left of the browser
   tab you want the AI to edit. Keep that tab open. **Stop sharing** revokes this
   tab's connection. Refreshing the page turns sharing off; other tabs stay private.

When finished, close the BAT window. The companion closes its own gateway and
Funnel; it also stops if ComfyUI remains offline for 15 seconds. ComfyUI startup
has a three-minute timeout. A duplicate BAT launch does not create another MCP
connection. Tailscale's base application/service can stay running for other
uses, but this MCP's public forwarding only runs with the ComfyUI session.
The fixed URL and its private token stay unchanged while offline.

The gateway uses system Python 3.12; ComfyUI keeps its portable Python. Only
small launch hooks were added to the three BAT files. Their backups are under
`mcp_service/.local/launcher-backups`; the companion implementation stays inside
the Arkennemasis node pack.

Check the connection without generating anything:

```powershell
python mcp_service/launch.py doctor
```

The report separates gateway configuration, ComfyUI connectivity, bridge
availability and connected canvases. A configured web URL is not proof of a
successful connection from an AI client. The local health address is
`http://127.0.0.1:8190/health`; localhost addresses cannot be used by web AI clients.

## Connect a local AI client

The simplest local configuration uses **stdio**: the AI client launches the
gateway itself, so you don't also have to run `serve` manually.

For Codex, add a separate MCP server named `arkennemasis`. Use your existing
working Python executable and these arguments:

```toml
[mcp_servers.arkennemasis]
command = 'C:\Users\hisha\AppData\Local\Programs\Python\Python312\python.exe'
args = ['E:\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable\ComfyUI\custom_nodes\comfyui-arkennemasis\mcp_service\launch.py', 'serve', '--transport', 'stdio']
startup_timeout_sec = 30
tool_timeout_sec = 60
```

For Claude Desktop or another client with JSON MCP configuration:

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

These are examples for this installation; existing AI-client settings are not
automatically changed. Reload/restart the AI client after adding its server.
Keep ComfyUI running. The gateway needs no model-provider login of its own.

Local clients that support HTTP bearer headers can instead connect to
`http://127.0.0.1:8190/mcp` with `Authorization: Bearer <local_token>` while `serve`
or `web` is running in local-token or connection-link mode. To start only the
local gateway, use `python mcp_service/launch.py serve`. The generated token lives
in `mcp_service/.local/config.json`.
Keep that file private. The separate `bridge_token` is only for gateway-to-ComfyUI
traffic; never give it to an AI client or copy it into a web form.

## Connect ChatGPT web or Claude web with No Auth

This installation uses a fixed HTTPS connection without an OAuth provider.
The connection URL contains a private access secret; **No
Auth** means the AI client does not add a separate authentication header. Anyone
with the complete URL can use this gateway, so keep it out of screenshots,
shared conversations and public repositories.

1. Launch your usual ComfyUI BAT and wait for **Connection: online** in its badge.
2. Open `mcp_service/.local/connection.txt` and copy its complete URL. It has the
   shape `https://hisham.tailb871a6.ts.net/connect/<private-secret>/mcp`.
3. In ChatGPT's plugin form, choose **Server URL**, paste that complete URL,
   and choose **No Auth**. Create the plugin and enable it in the conversation.
   In Claude's remote connector form, use the same complete URL and select no
   authentication if its form offers that setting.
4. For live workflow editing, click **Share canvas** in the Arkennemasis MCP
   badge inside the particular ComfyUI browser tab you want to share.

The URL stays the same across normal restarts. Renaming or deleting the Tailscale
machine/tailnet, or rotating the private connection token, changes it. An offline
computer makes the URL temporarily unreachable; it does not create a new URL.
[Stable Funnel names](https://tailscale.com/docs/reference/examples/funnel)

Close the ComfyUI BAT window to stop this installation's connection. Relaunch
that BAT to reconnect at the same URL. You do not need to recreate the plugin
after normal restarts. The companion removes only its own foreground Funnel
mapping and closes only the gateway it started; unrelated Tailscale connections
are left alone. Do not use `funnel reset` for this task.

Launcher diagnostics are in `mcp_service/.local/companion.log`; gateway messages
may also appear in `.local/web.log`. `.local/session-status.json` records the
current state and heartbeat. The badge refreshes every five seconds and treats
an active heartbeat older than 15 seconds as stopped. Its connection indicator
is separate from **Share canvas**, which controls access to the open workflow.

The gateway accepts MCP calls only on the secret connection path; it does not
publish an unprotected `/mcp` endpoint or ComfyUI's full port 8188. The launcher
prints the connection file's path, never the secret URL, and suppresses child
request logs. Local stdio clients can still be used. This mode is separate from
the OAuth configuration below.

### Advanced: configure a manually managed fixed connection

This is an alternative deployment for another installation, not the daily
startup procedure for this computer. Do not run it alongside the BAT companion.

Use an existing Tailscale login and inspect `tailscale funnel status --json`
before changing mappings. The gateway port is 8190. Configure public HTTPS
forwarding and save the resulting hostname, replacing the example below with
that computer's actual Funnel hostname:

```powershell
tailscale funnel --https=443 http://127.0.0.1:8190
```

Keep that foreground tunnel terminal open. In a separate terminal, run:

```powershell
python mcp_service/launch.py configure-web --public-url https://YOUR-MACHINE.YOUR-TAILNET.ts.net
python mcp_service/launch.py web
```

Initial Funnel setup may require approval in Tailscale's browser page.
`configure-web` preserves the existing private connection token and local/bridge
credentials. With a fixed origin configured, `web` starts only the gateway and
does not create or modify any external tunnel. Stop a manually launched `web`
process with Ctrl+C or `python mcp_service/launch.py stop-web`, and close the
separate foreground Funnel terminal. [Funnel setup](https://tailscale.com/docs/features/tailscale-funnel),
[Funnel CLI](https://tailscale.com/docs/reference/tailscale-cli/funnel)

A named Cloudflare Tunnel is another fixed-hostname option when you have a
domain on Cloudflare. Forward it to the same gateway port and use
`configure-web --public-url` with that hostname. A fixed URL does not require
OAuth. [Cloudflare setup](https://developers.cloudflare.com/tunnel/setup/)

### Optional temporary connection

When `public_url` is empty, `web` uses the optional `cloudflared` executable from
PATH or `mcp_service/.runtime/bin`. It creates a Quick Tunnel and writes its
temporary private URL to `connection.txt`. This fallback is not used by the
fixed setup on this computer. Quick Tunnels are free and intended for development/testing. They
assign a temporary hostname, allow up to 200 concurrent requests and have no
uptime guarantee. They do not support SSE; this gateway uses JSON responses over
Streamable HTTP. Each restart changes the temporary hostname.
[Cloudflare Quick Tunnel documentation](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)

### Data transfer

This setup does **not** promise unlimited bandwidth. Workflow JSON, prompts and
edits travel through the tunnel; models and generation run on your computer.
The AI receives only the results it requests. Images can be returned inline,
with a default 20 MiB limit per image; large video downloads are outside version
0.1. Heavy media transfer needs a separate storage/transfer design that fits the
provider's current terms. Your internet upload speed and AI-client limits still
apply.

Tailscale Funnel is available on all plans, including the free Personal plan for
non-commercial personal use. Funnel has non-configurable bandwidth limits;
neither a monthly unlimited-transfer allowance nor unlimited speed is promised
here. [Funnel limitations](https://tailscale.com/docs/features/tailscale-funnel),
[Tailscale pricing](https://tailscale.com/pricing)

## Optional OAuth for web clients

**A localhost URL is not a web connector URL.** For the common connection usable
by both providers, finish this installation-specific setup:

1. Choose a stable HTTPS hostname for the MCP gateway.
2. Configure an OAuth authorization provider that supports MCP discovery,
   authorization-code + PKCE S256, and client registration (CIMD, configured DCR,
   or pre-registered clients). The provider owns login, consent and token issuance.
3. Create an API resource with audience equal to your public MCP URL, for example
   `https://comfy.example.com/mcp`. Configure signed JWT access tokens and scopes
   `comfy:read`, `comfy:write`, `comfy:run`, and `comfy:media`. This gateway does not
   accept opaque access tokens.
4. Merge these real values into `mcp_service/.local/config.json`, preserving its
   generated local and bridge tokens:

   ```json
   {
     "auth_mode": "oauth",
     "public_url": "https://comfy.example.com",
     "issuer_url": "https://YOUR-OAUTH-ISSUER/",
     "jwks_url": "https://YOUR-OAUTH-ISSUER/.well-known/jwks.json",
     "audience": "https://comfy.example.com/mcp",
     "allowed_subjects": ["YOUR-EXACT-OAUTH-USER-ID"],
     "allowed_algorithms": ["RS256"],
     "scope_claim": "scope"
   }
   ```

   The examples above are placeholders, not provisioned services. The issuer must
   exactly match the token's `iss`, including its trailing slash. `allowed_subjects`
   restricts this installation to the owner accounts you explicitly list.
5. Run `doctor`, then restart the MCP gateway with `serve`.
6. Configure Tailscale Funnel or a named HTTPS tunnel to forward that hostname to
   `http://127.0.0.1:8190`. Forward the gateway, not ComfyUI's full port 8188.
   Keep `/mcp` and `/.well-known/*` reachable. Keep the Host header consistent
   with the configured public hostname or local gateway hostname.
7. In ChatGPT, enable Developer mode where available, create a plugin using
   **Server URL**, enter `https://comfy.example.com/mcp`, and select **OAuth**.
   Use the exact callback URI shown by its connection screen if registering a
   static OAuth client. Sign in as the allowed owner and enable the plugin in chat.
8. In Claude, add a custom remote connector with that same URL and complete the
   OAuth login. Configure its callback in the authorization provider when using
   pre-registered clients. Grant the scopes for the actions you intend to use.

Keep the PC awake, ComfyUI running, the MCP gateway running and the tunnel running.
Models/GPU stay local; requested workflow data and results go to the AI provider.

ChatGPT's **Tunnel** connection is an alternative: OpenAI's tunnel client can
launch this server through stdio. Configure it to use the same Python command
and arguments as the local stdio example, then select its tunnel ID in ChatGPT.
It requires OpenAI Platform tunnel permissions and a runtime key. This route
does not create a shared Claude web endpoint.

Primary connection references:

- [ChatGPT plugin connection](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [OpenAI OAuth contract](https://developers.openai.com/plugins/build/auth)
- [OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [Claude connector authentication](https://claude.com/docs/connectors/building/authentication)
- [Cloudflare named tunnel setup](https://developers.cloudflare.com/tunnel/setup/)

## Use it in conversation

Start with a read-only check:

> Use Arkennemasis MCP. Check that my ComfyUI is reachable and list the canvases
> that have enabled sharing. Read the selected canvas and explain its structure.

Then try a small edit on a duplicate/test workflow:

> Change the positive prompt to [your prompt], preserving the remaining settings
> and connections. Apply it to this canvas, validate it and show me what changed.

For generation:

> Run that workflow once. Track its job until it completes, then show the actual
> generated image.

For saved files:

> Duplicate [workflow name] as [new name], change [setting], and save it. Keep the
> original intact.

An AI client may ask its own confirmation before write/run tools. The gateway
enforces credentials/scopes independently of those confirmations.

## Tool behavior

| Tools | Behavior |
| --- | --- |
| `server_status`, `list_node_types`, `get_node_schema` | Live backend and installed node inspection |
| `list_workflows`, `read_workflow` | Saved documents, including their format and revision |
| `save_workflow`, `patch_workflow`, `restore_workflow` | Revision-checked writes with backup snapshots |
| `validate_prompt` | ComfyUI validation without queuing, using the loaded bridge |
| `queue_prompt`, `get_job`, `cancel_pending_job` | Explicit API-prompt execution, durable retry protection, exact pending-job cancellation |
| `list_outputs`, `get_output_image`, `upload_reference_image` | Output descriptors, inline image bytes, reference import |
| `list_canvases`, `read_canvas`, `apply_canvas`, `patch_canvas`, `undo_canvas` | Explicit opted-in browser session, revision checks and browser acknowledgment |

Saved UI JSON and executable API prompt JSON are different. `read_canvas` uses
ComfyUI's frontend to return both. Saving a file does not edit the open canvas;
applying a canvas edit does not silently overwrite a saved workflow file.
An applied edit marks that workflow as modified in ComfyUI, so save it normally
when you want to keep the change. Sharing applies to that browser page's active
workflow; switching its workflow changes the canvas visible through that session.
Refreshing or closing the page ends sharing.

Use one canonical UUID `request_id` per intended generation. Reuse it on retries.
If an HTTP response is lost, the persisted ledger refuses to resubmit and checks
the exact job ID. An uncertain submission must be inspected before you request
a genuinely new run. Error/validation results are never represented as success.

## Fresh installation and development

Use a separate system Python 3.12 for the optional gateway:

```powershell
python -m pip install --target mcp_service/.runtime -r mcp_service/requirements.txt
python mcp_service/launch.py init
```

To run the development checks:

```powershell
python -m pip install --target mcp_service/.runtime -r mcp_service/requirements-dev.txt
python mcp_service/launch.py test
node --test tests/mcp/frontend.test.cjs
```

The test bridge needs `aiohttp` in the optional test environment. It is already
available inside ComfyUI itself and is not a new ComfyUI runtime requirement.
Tests use scratch directories and mock servers. They do not queue GPU work.
The BAT companion update passed 88 of 89 Python tests, with one Windows symlink
permission check skipped, and all 12 frontend tests. Lifecycle checks verified
that closing the owner process stops the companion and gateway. A separate
live check confirmed that ending the foreground Funnel process removes its
mapping. The public endpoint returned all 20 tools and rejected unprotected
paths after the lifecycle migration.

With ComfyUI and the gateway running, check the real local MCP connection:

```powershell
python tests/mcp/smoke_local.py
```

For an intentional generation check, add `--run`. It creates a new two-node
test workflow, validates it, runs one 64 × 64 image and verifies the returned
image bytes. It also checks workflow save/patch/restore and duplicate-request
protection. The check refuses to run while ComfyUI's queue is busy. The test
workflow is saved under `Arkennemasis MCP` and its image under
`ComfyUI/output/arkennemasis_mcp_test`. This is a real run, unlike the isolated
test suite.

With the current web connection running and that test job still in ComfyUI's
history, check public HTTPS access without generating anything:

```powershell
python tests/mcp/smoke_public.py --public-dns
```

This reads the private connection file without printing its URL, checks all 20
tools and real image retrieval, and confirms unprotected routes return 404.
`--public-dns` checks Funnel through its public internet relay while retaining
TLS hostname verification, even when run from this Tailscale-connected computer.
Those public checks passed for the configured fixed URL.
If ComfyUI has restarted or cleared its history, run the intentional local
generation check again first. The build also verified live canvas editing and
undo in ComfyUI, including its unsaved-change indicator. Connection and tool
invocation from your signed-in ChatGPT or Claude account still need the final
connector setup in that client.

See [architecture](architecture.md) for ownership boundaries and extension rules.

## Troubleshooting

- **Connection refused:** start the process for the missing endpoint. 8188 is
  ComfyUI; 8190 is the MCP gateway.
- **Web connector cannot reach localhost:** use the complete HTTPS URL from
  `connection.txt`. This installation's fixed URL survives normal restarts;
  only the optional Quick Tunnel fallback gets a new hostname on restart.
- **Fixed URL offline:** launch the usual ComfyUI BAT, keep its window open and
  check the badge's connection line. Hover for details; check Tailscale is
  connected. Read `.local/companion.log` and `.local/session-status.json` for
  startup errors. Recreating the ChatGPT plugin does not start an offline service.
- **Connection error / port already shared:** another gateway or Funnel mapping
  is using the required port. The companion leaves it intact. Stop only the
  conflicting MCP process/mapping you recognize, then relaunch the BAT.
- **Connection badge missing:** restart ComfyUI to load the new local route, then
  refresh the browser after saving your work. Canvas sharing remains separate
  from connection readiness.
- **Bridge unavailable / 404:** restart ComfyUI after installation, check the
  Arkennemasis startup message, then refresh the browser after saving its work.
- **No canvases:** enable canvas sharing in the intended tab and keep it open.
- **401 / connection link rejected:** copy the entire current connection URL for
  No Auth mode, including its secret path. Local-token HTTP requires its bearer
  token; OAuth mode requires a valid JWT with the configured issuer, audience,
  expiry and owner allowlist.
- **Insufficient scope:** reauthorize with the scope for the requested action.
- **421:** the tunnel is forwarding an unexpected Host header. Match the configured
  hostname; don't turn off host validation.
- **Revision conflict:** read again and reapply the intended change to the current
  revision. This prevents overwriting intervening manual edits.
- **Canvas timeout:** check the tab and read the canvas again. A lost acknowledgment
  does not prove the change failed to apply.
- **Image missing:** check `get_job` and `list_outputs`; queued or failed jobs do
  not guarantee outputs. Large video files are not inline-image results.
