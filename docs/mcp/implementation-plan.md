# MCP public release implementation

Implementation status: **0.2.0**, verified locally on 24 September 2026.
The implementation stays in the existing Arkennemasis package and preserves its
other nodes and private catalogues. OAuth was the final feature phase.

## Implemented

- [x] Fix identified connection-loss paths, canvas expiry and ambiguous edit retries.
- [x] Add layered status, transition diagnostics and fault/recovery tests.
- [x] Add installation capability scopes and protected source paths.
- [x] Add source search/read/edit/create/rename/trash, revisions and restore.
- [x] Add node scaffolding, static validation and selected-file node tests.
- [x] Add backend diagnostics and controlled restart with a stable launcher supervisor.
- [x] Add defined Git operations and reviewed dependency installation plans.
- [x] Add queue control, shared-canvas execution, progress/previews and memory controls.
- [x] Add model/media inventory, metadata, controlled downloads and file management.
- [x] Add validated launch settings and backups.
- [x] Add portable setup, isolated optional dependencies and owner-facing setup UI.
- [x] Keep ordinary portable BATs separate from the sole opt-in fast-FP16 MCP launcher.
- [x] Prepare documentation, existing-license guidance and automated CI configuration.
- [x] Add external-provider OAuth configuration, scoped clients and local revocation.
- [x] Run automated tests, fresh optional-runtime installation and a bounded live generation test.

## Verification and remaining activation

See the [release verification record](release-verification.md) for exact results
and limitations. A full managed ComfyUI restart, sustained public reliability,
and a real external-provider OAuth login remain separate live acceptance checks.
The live generation test used an existing backend and left it running.

Existing installations use **3. Create launchers**, then close the active session
and relaunch through `run_nvidia_gpu_fast_fp16_accumulation_with_mcp.bat` to load the restart supervisor.
The original BAT starts ordinary ComfyUI without automatically starting MCP or
Funnel. Preserve unsaved workflows before
refreshing the browser. OAuth is configured and enabled explicitly; existing
private-link connections are not automatically migrated. GitHub publication and
a hosted CI run have not been performed by this implementation work.

## Connection findings addressed

The former browser bridge expired sessions after 60 seconds even with a connected
WebSocket; background tabs can throttle HTTP heartbeats. It also discarded
session identity immediately after a brief socket loss. Connected sessions now
remain registered, with a grace period and authenticated resume after disconnect.

The former companion stopped both gateway and Funnel after brief backend failures
or one failed Tailscale probe. Health and recovery are now tracked independently,
with bounded retries. A transient Windows status-file replacement error no longer
ends supervision. Windows backend restart now uses a persistent supervisor and
fixed worker instead of replacing the BAT-owned process with an orphaned child.

These are identified failure paths, not proof of the cause of every screenshot
or a guarantee against provider, network, sleep or client-availability failures.

## Boundaries

Capability/path restrictions constrain MCP operations; they do not sandbox
Python node execution at the operating-system level. Development is opt-in per
installation and source pack. Credentials, private catalogue files and MCP
enforcement files are excluded from ordinary source tools. Model downloads
are explicit operations; core ComfyUI receives no internet-request code.

No general shell, arbitrary HTTP proxy or blanket filesystem access is exposed.
Ordinary backend crashes remain stopped for diagnosis; only an authenticated,
recorded restart request can trigger the managed restart path.

Deferred: core/frontend automatic updates, advanced profiling, partial graph
execution, universal hot reload, package uninstall and bulk media streaming.
Client-dependent media presentation and provider authentication require their
own integration checks.
