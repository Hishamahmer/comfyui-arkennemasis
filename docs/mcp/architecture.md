# MCP architecture and ownership

The implementation lives in the Arkennemasis custom-node package. Three small
hooks in the portable ComfyUI BAT launchers invoke its companion; the original
BAT files are backed up under `.local/launcher-backups`. ComfyUI's execution
engine, model loading and existing node APIs are unchanged.

| Owner | Responsibility | Imports |
| --- | --- | --- |
| `mcp_service/settings.py` | Explicit installation configuration, validation and first-run secrets | Standard library |
| `mcp_service/server.py` | MCP transport, authentication wiring and service lifecycle | Optional MCP runtime |
| `mcp_service/tunnel.py` | Gateway startup for fixed external tunnels, optional Quick Tunnel, connection file and owned-child cleanup | Standard library, optional cloudflared executable |
| `mcp_service/companion.py` | BAT session ownership, ComfyUI readiness, gateway and foreground Funnel startup/cleanup, status heartbeat | Standard library, Windows process APIs, Tailscale executable |
| `mcp_service/tools.py` | Tool schemas, descriptions, annotations and scope enforcement | Optional MCP runtime, domain modules |
| `mcp_service/auth.py` | Local bearer checks and external-issuer JWT verification | Optional HTTP/JWT/MCP dependencies |
| `mcp_service/workflows.py` | Confined workflow files, JSON Patch, revisions and snapshots | Standard library |
| `mcp_service/client.py` | ComfyUI's local HTTP API, queue/history and media requests | HTTP client |
| `mcp_service/assets.py` | Validated media descriptors and output discovery | Standard library |
| `mcp_service/execution.py` | Durable generation-request identity and retry behavior | Standard library |
| `mcp_service/comfy_bridge.py` | ComfyUI-local authenticated control routes, browser request broker and restricted connection status | ComfyUI, aiohttp, standard library |
| `web/mcp/` | Connection readiness badge, opt-in canvas sharing, serialization, revision checks, apply/undo and acknowledgment | ComfyUI frontend |
| `tests/mcp/` | Storage, auth, transport, execution and bridge verification | Isolated test runtime |

## Processes

ComfyUI imports only the lightweight bridge. The official MCP SDK and its
dependencies are isolated under `mcp_service/.runtime` and loaded only by
`mcp_service/launch.py`. Importing `mcp_service` itself has no startup or network
side effects. The gateway is started explicitly and binds to loopback.

The gateway can expose stdio for trusted local MCP clients or authenticated
Streamable HTTP for a local client/tunnel. A tunnel and OAuth issuer are
deployment components, not model execution hosts.

On this computer, each supported portable BAT invokes `companion.py start`
using portable Python. It launches a separate watcher with system Python 3.12
and the BAT's CMD process ID. The watcher holds a Windows process handle to
that owner and a single-instance file lock. It waits up to 180 seconds for
ComfyUI, verifies the saved Tailscale hostname and unused gateway/HTTPS ports,
then starts its own loopback gateway and a foreground Tailscale Funnel. It
waits for both before publishing an online heartbeat. The fixed `public_url`
and private connection token are preserved.

The watcher closes its two children when the BAT window closes, when ComfyUI
has remained offline for 15 seconds, or when a child fails. Every ten seconds,
it also checks Tailscale's running state, hostname and expected public route;
a failed check records an error and closes its children. Ending foreground
Funnel removes only that session's forwarding. An existing gateway or HTTPS
mapping causes an error instead of being replaced. The Tailscale base service
is not stopped. The old Windows sign-in task is disabled and its persistent
background Funnel mapping has been removed; this MCP's connection is now tied
to the ComfyUI session. A second BAT cannot start a duplicate watcher.

The advanced `web` command remains available for a separately managed external
tunnel. With a fixed `public_url` in connection-link mode, it starts only its
own gateway and preserves the configured hostname and private token. It is not
the default BAT startup path and should not run alongside the companion.

When `public_url` is empty, `web` instead creates a temporary Cloudflare Quick
Tunnel. Both modes refuse an occupied gateway port, write the connection file
and stop only their owned children on interruption, stop request or child failure.
The fixed mode's connection file does not prove external tunnel reachability;
public integration checks verify that separately. No gateway starts implicitly
when ComfyUI imports the node pack, and port 8188 is never published.

## Authentication boundaries

1. The local AI client uses stdio or the local HTTP bearer token.
2. In connection-link mode, a web AI client uses the complete secret URL with
   the connector set to No Auth. Possession of that URL grants all four MCP
   scopes. The gateway compares the private path before routing the request;
   requests to other public paths cannot invoke tools. It does not log the
   secret URL. Local HTTP bearer access remains available in this mode.
3. In OAuth mode, a web AI client uses JWTs from a configured issuer. The gateway checks
   signing key/algorithm, issuer, audience, expiry, subject allowlist and scopes.
   Login, client registration, token issuance and account revocation belong to
   the established authorization provider. Short-lived JWTs expire normally;
   this resource server does not introspect opaque tokens or instantly detect
   provider-side revocation of a still-valid JWT.
4. The gateway calls bridge control routes over loopback with a separate
   installation secret. The public gateway exposes no general ComfyUI proxy.
5. An opted-in local browser receives its own session secret, not the gateway
   secret. Browser routes enforce origin/host and request-shape checks. Each
   control request names its target session and a unique request ID.

Canvas sharing is a per-page button, off by default and reset by refresh. It is
not a persisted global preference. Shared sessions follow their page's active
workflow, and each workflow has a distinct live revision identity. Applying an
edit preserves the saved baseline and updates ComfyUI's modified state without
triggering automatic generation. The frontend keeps a bounded undo history for
MCP edits; writing a saved workflow remains a separate operation.

Connection readiness is independent of canvas sharing. The local browser's
read-only status route uses the same loopback, origin and custom-header checks
as other browser routes. It returns only state, message and update time from
the session status file, never the private URL or credentials. The badge polls
every five seconds; an active heartbeat older than 15 seconds is treated as
stopped. It does not register or share a canvas while checking readiness.

Existing `codex_provider/auth.py` is outbound model-provider authentication. It
does not participate in MCP access and must not be reused as an MCP token store.

## State

`mcp_service/.local/` contains private config, the current connection URL,
workflow snapshots and the SQLite job ledger. `launcher.json` holds the local
Python/Tailscale executable paths, `session-status.json` holds the companion's
state and heartbeat, and `companion.log` records launcher diagnostics. Gateway
messages may also appear in `web.log`. The companion's singleton lock and BAT
backups remain in this private directory. Fixed URLs retain their identity
across restarts but are reachable only while the gateway and tunnel are online.
Temporary Quick Tunnel URLs expire when their tunnel stops. Private state and
`.runtime/` are ignored by Git. Existing user workflows stay
in the configured ComfyUI workflow directory; no copy is made into source code.
Never commit real tokens, private prompts, generated outputs or runtime packages.

Saved-document revisions use a deterministic content hash. Live revisions are
owned by the browser bridge. Never compare them as interchangeable identifiers.
UI JSON stays UI JSON; the browser's `graphToPrompt` produces the API prompt.

## Extend deliberately

New tools belong to the existing owning module and must declare a scope and
accurate read/write annotations. Keep functions small and call established
ComfyUI interfaces. Add another module only for a distinct responsibility.

Future maintenance tools need explicit target-specific operations and their own
scope. They must not turn the gateway into an arbitrary command executor. Model
downloads, custom-node installation and service restarts are not in version 0.1.

Before claiming a new capability ready, test the state change and the returned
result through the MCP transport. For canvas behavior, verify a real frontend
acknowledgment; a saved JSON file is not sufficient evidence.
