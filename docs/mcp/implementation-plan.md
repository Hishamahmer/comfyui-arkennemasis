# MCP public release implementation

Approved scope: reliable web access, scoped node development and maintenance,
guided setup, and an organized release in the existing Arkennemasis package.
OAuth implementation is the final feature phase. Existing nodes and private
catalogues are preserved. No general shell or arbitrary HTTP proxy is exposed.

## Work sequence

- [ ] Diagnose and fix connection loss, canvas expiry and ambiguous edit retries.
- [ ] Add layered status, transition diagnostics and fault/recovery tests.
- [ ] Add installation capability scopes and protected source paths.
- [ ] Add source search/read/edit/create/rename/trash, revisions and restore.
- [ ] Add node scaffolding, static validation and execution verification tools.
- [ ] Add backend diagnostics, controlled restart and recovery.
- [ ] Add defined Git operations and reviewed dependency installation plans.
- [ ] Add queue control, shared-canvas execution, progress/previews and memory controls.
- [ ] Add model/media inventory, metadata, controlled downloads and file management.
- [ ] Add validated launch settings and backups.
- [ ] Add portable setup, optional dependencies and owner-facing setup UI.
- [ ] Prepare documentation, examples, licensing and publication checks.
- [ ] Finish OAuth, scoped clients/revocation and authenticated onboarding.
- [ ] Run fresh-install, integration, sustained reliability and release checks.

## Confirmed audit findings

The browser bridge expires a session after 60 seconds even when its WebSocket
is connected; background tabs can throttle the HTTP heartbeat. It also deletes
session identity immediately when a socket briefly disappears. The companion
stops both gateway and Funnel after 15 seconds of ComfyUI HTTP failures or one
failed Tailscale probe. These are reproduced code paths; screenshots alone do
not establish which one caused every reported incident.

The live installation was reachable at the start of the audit, with one shared
canvas and an idle queue. No user workflows were edited or queued for this audit.

## Boundaries

Capability/path restrictions constrain MCP operations; they do not sandbox
Python node execution at the operating-system level. Development is opt-in per
installation and source pack. Credentials, private catalogue files and MCP
enforcement files are excluded from ordinary source tools. Model downloads
are explicit operations; core ComfyUI receives no internet-request code.

Deferred: core/frontend automatic updates, advanced profiling, partial graph
execution and universal hot reload. Documentation will identify tested platforms
and separate tested functionality from client-dependent media presentation.
