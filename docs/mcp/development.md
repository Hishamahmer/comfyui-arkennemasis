# MCP development and release checks

The MCP is part of this repository; it has no separate copy of the pack, models or user workflows. Keep runtime state private and preserve the other node modules when changing it. See [architecture](architecture.md) for module ownership.

## Automated checks

Use a disposable Python environment from the repository root:

```sh
python -m venv mcp_service/.runtime/test-environment
```

Activate it using your platform's normal virtual-environment activation command, then run:

```sh
python -m pip install -r mcp_service/requirements-dev.txt
python -m unittest discover -s tests/mcp -v
node --test tests/mcp/frontend.test.cjs
```

Do not commit the test environment. Alternatively, when gateway/test dependencies are already installed in its isolated target:

```sh
python mcp_service/launch.py test
node --test tests/mcp/frontend.test.cjs
```

The suite uses temporary directories and mocked services. It does not generate with the GPU or need an AI login. Windows process-companion tests are skipped on other platforms; symlink tests may be skipped if the local account lacks that permission. Skips are limitations of that run, not successful coverage of the skipped behavior.

The MCP CI workflow covers Windows and Linux on Python 3.11/3.12, plus Node frontend tests. An added workflow is not proof of a passing hosted CI run: inspect the actual run when it executes. Platform process ownership and browser/tunnel integration still need live checks.

## Live checks are explicit

With ComfyUI and the local gateway running:

```sh
python tests/mcp/smoke_local.py
```

For an intentional generation test, use:

```sh
python tests/mcp/smoke_local.py --run
```

The `--run` test creates a two-node 64 × 64 workflow, validates and queues it, checks image bytes, and exercises saved workflow revisions/retry behavior. It refuses a busy queue. Its workflow and output use dedicated Arkennemasis MCP test names/folders. It is a real write and generation, unlike the normal unit suite.

For the current private-link connection, the read-only public check is:

```sh
python tests/mcp/smoke_public.py --public-dns --skip-image
```

This uses the private endpoint from `.local/connection.txt`, tests MCP and route protection through public DNS, and does not print the secret URL. The check is designed for private-link mode; OAuth needs a separate authenticated client check. Passing from this computer is not equivalent to a successful ChatGPT/Claude connection or a sustained reliability test.

Manually verify the setup dialog in a local browser, sharing/revocation, a harmless edit on a test canvas, stale-revision rejection, background-tab reconnect, owner-close cleanup, restart readiness and failed-start diagnostics. Preserve user canvases and do not run a generation unless intentionally testing one.

For the supported Windows portable installation, the release smoke test can own a temporary launcher session:

```sh
python tests/mcp/smoke_release.py --start-portable
```

This uses `run_nvidia_gpu_fast_fp16_accumulation_with_mcp.bat` to explicitly start ComfyUI and the gateway, runs the small image test, checks managed restart, and cleans up its own processes. Create the fast-FP16 with-MCP launcher through local setup first. It refuses to take over existing processes on the configured ports. To use an already-running idle ComfyUI backend while owning only a temporary gateway, use `--use-running-comfy` instead; that mode deliberately skips backend restart. Both modes write private reports under `.local/` and create the dedicated test workflow/image.

See the [0.2.0 local verification record](release-verification.md) for checks actually completed and remaining integration limits.

## Repairing the optional runtime

Gateway dependencies are installed under `mcp_service/.runtime/pyXY`, where XY is the selected interpreter's major/minor version. Older installations may use `.runtime` directly. `launch.py` prefers a matching versioned directory.

If a versioned installation is damaged, stop the gateway/with-MCP BAT, preserve `.local`, and rename only the affected `.runtime/pyXY` folder to a backup name. Reopen ComfyUI using its ordinary BAT, open local MCP setup, and install its dependencies again using the same interpreter. The installer stages packages, checks imports and only then moves the new directory into place. Retain the backup until startup succeeds. Do not delete `.local`, which holds credentials, connection identity and recovery state.

For a manually installed legacy target, stop its gateway and rename `.runtime` to a backup, then reinstall using the documented `pip --target mcp_service/.runtime` command. Avoid mixing binaries installed for different Python versions. The gateway repair does not repair or roll back ComfyUI's separate Python environment.

## Publication checklist

1. Run the automated suite for the change and inspect skips/failures. Record exact tested versions and whether live/frontend/provider checks ran.
2. Review `git status` and `git diff --check`. Include only intended public files; preserve other in-progress node work.
3. Inspect tracked paths. `.local/`, `.runtime/`, `.env`, generated outputs and private catalogues must not be published. `.gitignore` does not untrack files previously committed, so check `git ls-files` as well.
4. Ensure examples use placeholders and contain no personal hostname, absolute workstation path, access token, private prompt, package plan or model data.
5. Keep the [MIT license](../../LICENSE) and existing pack organization. Check dependency licenses separately if vendoring third-party packages; the runtime is not a release artifact.
6. Review the existing registry workflow before changing `pyproject.toml`: a version-file push on its configured branch can publish the pack. A test workflow alone must never publish it.
7. Verify a fresh clone without private modules, configure a test installation, and test the intended AI-client authentication flow before claiming one-click readiness for that client/platform.

The current MCP changes do not themselves commit, push, publish a GitHub release or provision a user's OAuth/tunnel account. Use the repository's normal review/release process after the checks pass.
