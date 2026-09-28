# MCP 0.2.0 local verification

Recorded 25 September 2026 for checks completed on 24 September 2026, except the
public-route observations explicitly dated below. This records local evidence;
it is not a claim that every installation or AI provider has been tested.

| Check | Observed result |
| --- | --- |
| Python automated suite | 289 tests: 284 passed, 5 skipped because this Windows test account could not create symlinks. |
| Frontend automated suite | 19 tests passed. |
| Restart ownership and recovery | Focused tests passed, including real disposable worker processes controlled by a persistent supervisor. A full live ComfyUI restart was not performed. |
| Optional runtime installation | A fresh Python 3.12 gateway dependency target installed and passed import checks. ComfyUI's separate Python environment was unchanged. |
| Local setup screen | Rendered in a fresh browser context with no JavaScript errors. No existing user canvas was changed. |
| Running gateway | Reported version 0.2.0 and exposed 58 MCP tools; selected source and maintenance access worked. |
| Real workflow execution | Created, patched and restored a dedicated test workflow, generated a 64 × 64 PNG and returned its image bytes through MCP. Reusing the request UUID did not queue a second job. |
| Test cleanup | Stopped the test-owned gateway; preserved the pre-existing ComfyUI backend. Test workflow and image remain in dedicated MCP test folders. |
| OAuth | Local configuration, verification, access-control and revocation tests passed. No external provider account was configured and no real provider login was completed. |
| Public route, 23 September | Six explicit public-DNS/TLS probes returned HTTP 200. A real connector invocation failed once and then succeeded on retry. This does not establish sustained reliability. |
| GitHub | CI configuration and release instructions prepared locally. No hosted CI run or GitHub publication was completed by this work. |

The live generation used built-in image nodes and an idle queue. It did not load
a diffusion/video model or edit the user's shared canvas. The test refused to
take over an already-running backend, so managed backend restart remains a live
acceptance check after launch through the separate `*_with_mcp.bat`.

Private test logs, process records and connection credentials remain under
`mcp_service/.local/` and are excluded from publication. Do not attach that
directory to a bug report; use a redacted diagnostic extract instead.

## Activate an updated installation

The fast-FP16-only launcher change was verified on 25 September: the Python run
passed 289 tests with 5 symlink-permission skips (294 total), including 19 setup
tests, and the 19 frontend tests passed. Hook migration, preflight, untouched
originals, flag preservation, repeated setup, custom-file protection and launcher
ownership were covered. Setup against copies of this installation's BAT files
made no changes; the superseded CPU and standard-GPU companions were removed
from the portable folder. This did not launch ComfyUI or repeat the live
restart/provider checks. See [agent handoff](handoff.md) for the remaining work.

1. In local **MCP setup**, use **3. Create launchers**. Save unsaved work, close the
   active ComfyUI launcher, and start
   `run_nvidia_gpu_fast_fp16_accumulation_with_mcp.bat`. This loads the supervisor
   and starts the connection. The original
   `run_nvidia_gpu_fast_fp16_accumulation.bat` starts the same mode without
   MCP/Funnel. Standard GPU and CPU launchers remain ordinary ComfyUI only;
   do not launch both modes at once.
2. Refresh the ComfyUI browser and inspect **MCP setup** for installation permissions
   and selected custom-node folders.
3. Reuse the existing complete private connection URL with **No Auth**, unless
   OAuth has been explicitly configured and enabled. Refresh the AI connector's
   tool list once after the upgrade; recreating it should not be needed for an
   ordinary restart with the same saved connection identity.
4. Choose **Share canvas** in the specific browser tab the AI should edit.

See the [installation and daily-use guide](../../setup_guides/01_arkennemasis_mcp_guide.md),
[OAuth setup](oauth.md) and [repeatable checks](development.md).
