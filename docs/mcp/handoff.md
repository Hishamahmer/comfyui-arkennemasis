# MCP agent handoff

Updated 25 September 2026. Continue in this repository's existing MCP modules;
preserve unrelated nodes, dirty work and private installation state.

## Implemented

Version 0.2.0 exposes 58 tools covering workflow/canvas edits and runs, scoped
custom-node source edits/backups/tests, defined Git and reviewed package changes,
model/media management, queue/progress/previews, diagnostics and managed restart.
Connection recovery addresses known canvas expiry, transient socket/backend
failures and Windows status-file errors. Optional external-provider OAuth setup
was implemented last; the existing private-URL mode remains in use unless the
owner explicitly switches it.

## Latest launcher change

- Original portable BATs (`run_nvidia_gpu.bat`, `run_cpu.bat`, and
  `run_nvidia_gpu_fast_fp16_accumulation.bat`) launch ComfyUI directly with their
  original flags. They do not automatically start MCP or Funnel.
- **3. Create launchers** creates only
  `run_nvidia_gpu_fast_fp16_accumulation_with_mcp.bat`. This starts the companion
  and stable backend supervisor, applies saved MCP launch overrides, and supports
  managed restart. No standard GPU or CPU MCP companions are created. Setup
  leaves original BATs byte-for-byte unchanged unless an earlier injected hook
  must be removed (with a private backup), so it does not rewrite the BAT
  running the current session. Custom/unrecognized companion files are not
  overwritten.
- `run_cpu_with_mcp.bat` and `run_nvidia_gpu_with_mcp.bat` from the superseded
  three-pair version were moved to the Windows Recycle Bin on 25 September.
  That version was never published, so setup carries no code to remove them.
- Close the active launcher before switching. Closing a with-MCP BAT ends only
  its owned gateway/Funnel. The Tailscale system service is unchanged. Ordinary
  BATs do not terminate manually started connections; setup/sharing controls
  remain part of the local custom-node pack.
- Runtime changes belong to `installation.py`, `companion.py`, `launch_backend.py`
  and their focused tests; user instructions are in the
  [setup guide](../../setup_guides/01_arkennemasis_mcp_guide.md).

## Evidence and remaining work

The [verification record](release-verification.md) records the 24 September
baseline: 284 Python tests passed, 5 skipped; 19 frontend tests passed; a 58-tool
gateway completed a dedicated 64 × 64 generation/retry check. Those results
predate the separate-launcher change.

Fast-FP16-only validation on 25 September: the complete Python suite ran 294
tests, with 289 passing and 5 symlink-permission skips; the 19 frontend tests
passed. The 19 setup tests cover hook migration with backups, preflight before
any write, untouched originals, flag preservation, repeated setup, custom-file
protection and quoted/unquoted owner paths. Setup run against copies of this
installation's real BAT files reported no changes. No live ComfyUI process was
started or restarted for this launcher change. Private evidence is in
`.local/test-fast-fp16-only.log`.

An earlier expiry test was timing-sensitive; its clock is now controlled
explicitly. Preserve unrelated working-tree changes; these files have not been
committed or pushed by this work.

Remaining acceptance work: full managed live ComfyUI restart and owner-close
cleanup; sustained web-client/background-tab recovery checks; a clean-clone setup;
a real OAuth provider login; and reviewed GitHub publication plus hosted CI.
Check current processes and unsaved user work before disruptive tests. Keep OAuth
and GitHub activation distinct from code implementation. Do not promise zero
disconnections or automatic account setup.

Connection credentials, local config, private diagnostics and runtime packages
are under ignored `mcp_service/.local/` and `.runtime/`. Never publish them or paste
the full private URL into a handoff. No GitHub publication is claimed here.
