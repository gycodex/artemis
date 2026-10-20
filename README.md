# ARTEMIS · Direct-first Android testing

**Let your existing AI assistant operate Android devices, verify what happened, and leave a test report — without a separate Artemis model API key.**

[中文文档](README_CN.md) · [Quick start](#quick-start) · [Direct workflow](#direct-workflow) · [Regression cases](#regression-cases) · [Troubleshooting](#troubleshooting)

This fork of [Google ARTEMIS](https://github.com/google/artemis) focuses on **Direct / Host-Driven mode**. Connect an Android phone or emulator through MCP, use your current assistant to inspect and control it, and keep screenshots, checkpoints, logs, and optional recordings together.

Direct is the recommended workflow in this README. The CLI still defaults to Agent mode: **always specify `--type direct` when configuring Direct MCP.**

## Why Direct?

- **One reasoning loop.** Your MCP host interprets the screen and decides the next action. Artemis executes typed device tools; it does not start a second model.
- **Observe after acting.** Active test sessions save and return post-action observations by default, so a successful tap is not mistaken for a successful business flow.
- **Evidence-based results.** Record explicit checkpoints, distinguish failure from missing coverage, and export Markdown/JSON reports.
- **Less coordinate maintenance.** Match fresh UI elements by text, resource ID, content description, or package. Use explicit coordinate fallback only when no element matches.
- **Repeatable regression.** Turn verified interactions into JSON cases with bounded waits, fail-fast execution, failed-case reruns, and JUnit output.
- **Optional continuous video.** Record a session and browse actions, screenshots, checkpoints, and video segments in an offline timeline.

“No extra API key” means no Artemis model credentials are required for Direct's core device tools. Your host's subscription, model usage, and data policies still apply. Direct does not extract or reuse the host's login token.

## Direct vs. Agent

| | Direct / Host-Driven | Agent / Flash & Pro |
| --- | --- | --- |
| Who decides the next action? | The connected MCP host; a human in Web Direct; explicit steps in a JSON case | Artemis's configured model |
| Separate Artemis model setup | Not required for core Direct tools | Required: cloud credentials or a compatible local endpoint |
| Typical tools | `observe_screen`, `click_element`, `session_checkpoint`, `run_case` | `mobile_run_task`, `mobile_manage_task`, `mobile_inspect_trace` |
| Best fit | Interactive testing, debugging, evidence collection, deterministic regression | Delegated autonomous exploration and long-running jobs |
| Reports | Host-evaluated checkpoints and collected evidence | Profile-dependent planning, verification, and reporting |

Direct is not an autonomous Flash/Pro job in disguise. Keep the host connected for interactive testing; use the case CLI for predefined regression runs.

<a id="quick-start"></a>
## Quick start: connect a device to your assistant

### 1. Prepare the environment

Required: Git, Python **3.12+**, `uv`, Android Platform Tools (`adb`), and an authorized Android phone or a running emulator.

Optional:

- `scrcpy` and FFmpeg on `PATH` for Direct recording.
- Node.js/npm to build the Web console from source. MCP-only use does not need the Web console.
- A multimodal MCP host for screenshot-based interactive testing.

The examples below use PowerShell and this fork, which contains the Direct extensions described here.

```powershell
git clone https://github.com/gycodex/artemis.git
Set-Location ".\artemis"
uv sync
adb devices -l
```

For a USB phone, enable USB debugging and accept the authorization prompt. The device must appear as `device`, not `offline` or `unauthorized`.

For a TCP emulator such as MuMu, connect to its actual ADB endpoint:

```powershell
adb connect 127.0.0.1:7555
adb devices -l
```

`127.0.0.1:7555` is an example, not a universal MuMu port. Use the serial reported by your setup. If several devices are connected, choose one explicitly.

### 2. Install Direct MCP

Run **one** command for your chosen host, replacing the device serial:

```powershell
# Codex
uv run artemis mcp --type direct --device "127.0.0.1:7555" --install codex

# Claude Code / Claude Desktop
uv run artemis mcp --type direct --device "127.0.0.1:7555" --install claude
```

The installer updates the host's MCP configuration **and installs Artemis testing rules**. Other supported targets include `cursor`, `windsurf`, `vscode`, and `antigravity`. Review the generated configuration without installing it:

```powershell
uv run artemis mcp --type direct --device "127.0.0.1:7555" --generate-config codex
```

**Current Codex installer caveat:** its generated `enabled_tools` list contains the original Direct tools but omits six newer tools. Before reloading, add the following names to the existing list in the generated Artemis server configuration:

```text
recording_start
recording_stop
click_element
wait_for_element
wait_for_app
run_case
```

Keep the existing entries. Re-running the installer can regenerate the older list, so check it again after reinstalling. This affects host exposure, not the availability of these tools in the server or case CLI.

Reload the MCP server or restart your host after installation. Direct binds one device per server process; changing `--device` or `ARTEMIS_DEVICE_ID` requires restarting that server.

<a id="mcp-setup"></a>
<a id="mcp"></a>
For a custom MCP client, the stdio server entry point is:

```powershell
uv run python -m artemis mcp --type direct --device "127.0.0.1:7555"
```

This command starts a long-running MCP server, not an interactive chat or Web page. Normally your MCP host launches it.

### 3. Start with an observation

Ask your assistant:

> Use Artemis Direct to list devices, confirm the bound device is `127.0.0.1:7555`, and observe its current screen. Do not tap, type, or change app data yet.

Seeing `observe_screen`, `session_start`, and device-action tools indicates Direct mode. Seeing only `mobile_run_task` and related tools indicates Agent mode.

The MCP handshake does not require the device to be online; the first device operation still needs a working ADB connection.

<a id="direct-workflow"></a>
## From exploration to a test report

1. **Start a session:** `session_start(name, package_name, auto_observe=true)`.
2. **Observe:** `observe_screen` returns the current screenshot and available UI information.
3. **Act and verify:** use `manage_app`, semantic selectors, or device actions; inspect the post-action observation.
4. **Record assertions:** the host evaluates the result and calls `session_checkpoint`.
5. **Collect diagnostics:** use `read_logcat`; optionally start and stop recording inside the session.
6. **Finish:** `session_finish` writes the report and returns artifact paths.

Common tools:

| Purpose | Tools / behavior |
| --- | --- |
| Inspect | `list_devices`, `get_device_info`, `observe_screen`, `get_ui_hierarchy`, `take_screenshot` |
| Operate | `manage_app`, `click`, `click_sequence`, `long_press`, `swipe`, `input_text`, `press_key`, `open_link` |
| Locate reliably | `click_element`: exact AND matching; ambiguous or unavailable hierarchy is an error |
| Wait | `wait_for_element`: present / absent / enabled / stable; `wait_for_app`: foreground package |
| Track a test | `session_start`, `session_checkpoint`, `session_status`, `session_finish` |
| Diagnose / record | `read_logcat`, `recording_start`, `recording_stop` |
| Run a case | `run_case`: validated JSON steps, assertions, reports, and JUnit |

Coordinate actions use a **0–1000 normalized scale**, not physical pixels. Prefer a verified semantic selector where available. Element stability means stable node properties, not pixel-perfect visual stability.

### Example: RYB on MuMu

This is a suggested testing prompt, **not a claim that the current app build has passed**:

> On MuMu at `127.0.0.1:7555`, launch the installed app `com.ronghui.ryb` and start an Artemis Direct test session. Inspect the currently reachable startup, privacy-consent, and home screens; save screenshots and record separate checkpoints. Read package-related Logcat and generate a report. Do not clear data, reinstall, log out, submit consent, log in, send an SMS, or change account data. If prior consent or login makes a screen unreachable, record it as skipped or blocked and explain why; do not mark it passed. Request permission before any additional interaction needed to reach it.

App launch itself can trigger normal app/network behavior. A “read-only” test scope is an instruction to the operator, **not a technical sandbox enforced by Direct**.

## Checkpoints, verdicts, and artifacts

`session_checkpoint` accepts `outcome` values `passed`, `failed`, `blocked`, `skipped`, and `not_run`. Existing `passed=true/false` calls remain supported. Categories are `functional`, `visual`, and `performance`; these label checks and do not automatically measure performance.

Requesting `session_finish(status="passed")` cannot override failed checks:

- A failed checkpoint makes the result `failed`, unless explicitly aborted.
- No checkpoints, unverified checkpoints, or missing requested evidence make an otherwise passing result `incomplete`.
- `ok=true` from an action means it executed, not that a business requirement passed.
- Logcat is a bounded snapshot. No matching errors is not proof that no errors occurred.

Artifacts are stored in the configured Artemis traces directory. Use the paths returned by the tools:

| Artifact | When produced |
| --- | --- |
| `report.md`, `session.json`, `timeline.html` | Session finish, subject to successful evidence/report generation |
| `screenshots/` | Successful screenshot captures during the session |
| `logcat.txt` | Successful requested Logcat collection at finish |
| `video-NNN/` | Requested recording; requires scrcpy/FFmpeg |
| `case-results.json`, `junit.xml` | Executed JSON regression cases |
| `suite-results.json` | Case CLI summary, in the current directory by default |

Finished Direct sessions appear in Workspace history. Legacy history maps `incomplete` to `cancelled`, retaining the detailed Direct status.

### Recording and timeline

Call `recording_start` after `session_start`. `recording_stop` or `session_finish` finalizes the recording. Normal MCP shutdown aborts an unfinished session and attempts to finalize its owned recording; forced process termination cannot guarantee cleanup.

Open `timeline.html` locally to inspect events and seek the corresponding video segment. Copy the whole session folder to preserve relative links. Timing is anchored to action completion/evidence capture and the recorder's first-frame estimate; this is not an exact touch-time profiler or an automatic flicker detector.

<a id="regression-cases"></a>
## Turn verified interactions into regression cases

First explore the real app and confirm its selectors and transitions. Then author a JSON case with explicit waits and assertions. `run_case` validates all steps before execution, stops at the first failure, and marks remaining assertions `not_run`.

- At least one assertion is required.
- Only `wait_for_element` and `wait_for_app` can be assertion steps.
- Arbitrary shell/Python and nested sessions are not allowed.
- Include required preconditions in each case: failed-case reruns repeat the whole case.

See the [case schema and example](docs/direct-regression.md#executable-json-cases). After saving your verified case as `smoke.json`, run:

```powershell
uv run python -m artemis.mcp.direct_cases .\smoke.json --device "127.0.0.1:7555"

# Override a parameter defined by the case
uv run python -m artemis.mcp.direct_cases .\smoke.json --device "127.0.0.1:7555" --param "label=首页"

# Rerun cases that did not pass in the previous summary
uv run python -m artemis.mcp.direct_cases .\smoke.json --device "127.0.0.1:7555" --failed-from .\suite-results.json
```

A suite uses `{"cases": [...]}` with unique case names. The CLI needs no MCP host or model to execute these predefined steps. It exits nonzero if any executed case does not pass; incomplete evidence is also represented as a JUnit error.

## Web Direct: operate without an AI host

```powershell
uv run python -m artemis ui
```

Open [localhost:8000](http://localhost:8000), choose **Direct**, select a device, and start a session. The panel supports screenshot-based clicking, app launch, text/key/swipe actions, checkpoint outcomes and categories, recording, and report paths. A human makes the decisions; no model API key is required for Web Direct.

The Web UI and MCP server are separate processes. Restart the relevant process after updating code or configuration. Avoid running Web, MCP, and CLI controllers against the same device at the same time.

<a id="troubleshooting"></a>
## Troubleshooting

| Symptom | Check |
| --- | --- |
| Asked for a model API key | Confirm `--type direct`. Direct does not expose Agent's `mobile_diagnose`; use `uv run artemis doctor`. A missing model credential alone does not block core Direct tools. |
| New tools are missing | Check the Codex installer caveat above, the server's checkout, and host tool filters; reload the MCP server. |
| Device unavailable | Run `adb devices -l`; check authorization, emulator state, actual TCP endpoint, and configured serial. |
| Screenshot works but selectors fail | Inspect the hierarchy. Canvas-only or inaccessible content may need visual inspection and verified coordinates. Hierarchy failure must not count as element absence. |
| Recording cannot start | Check `scrcpy --version` and `ffmpeg -version`, then read the session's recording error. |
| Report says `incomplete` | Inspect unverified checkpoints and failed evidence collection. Do not force a pass. |

<a id="on-device-helper"></a>
### Device helper and sensitive data

Artemis may provision its Accessibility Helper to read UI hierarchies, with UIAutomator2 as a fallback. This can install a helper app and enable its service on the test device. See [helper management](artemis/interfaces/cli/commands/helper.py); `uv run artemis helper --help` lists commands. Set `ARTEMIS_HELPER_AUTO_INSTALL=false` to disable automatic helper installation, then restart the server.

Screenshots, UI text, action arguments, logs, and recordings may contain personal data or secrets. Use test accounts, review artifacts before sharing, and consider the MCP host's data handling. “No Artemis model key” does not mean that evidence never leaves the computer.

## Need autonomous execution instead?

Flash and Pro remain available in **Agent mode**:

```powershell
uv run artemis init
uv run artemis mcp --type agent --install codex
uv run artemis run "Open Settings and inspect the Battery screen" --profile flash
```

Agent mode needs its own model configuration. Flash is the reactive execution profile; Pro adds planning, verification, and richer diagnostics. Installing Agent for the same host changes its Artemis MCP entry, so choose the mode deliberately.

See the [MCP documentation](mcp_server/README.md), [testing rules](mcp_server/rules.md), and [Python client](packages/artemis-client) for the broader project.

## Project and license

- [Direct regression and evidence reference](docs/direct-regression.md)
- [This fork: issues and contributions](https://github.com/gycodex/artemis)
- [Official upstream: Google ARTEMIS](https://github.com/google/artemis)

Licensed under [Apache 2.0](LICENSE). Upstream ownership and notices are preserved. Third-party source is under [`third_party/`](third_party/), including portions of [Minitap mobile-use](https://github.com/minitap-ai/mobile-use) in [`third_party/mobile_use/`](third_party/mobile_use/), with its own license and metadata.
