# Direct regression and evidence

Direct remains model-free. Restart the Artemis MCP server after updating its
checkout to load the new tools. The Web Direct backend must also be restarted.

## Checkpoints and verdicts

Existing calls such as `session_checkpoint(name="Home", passed=true)` still work.
Alternatively, use `outcome`: `passed`, `failed`, `blocked`, `skipped`, or `not_run`.
Do not supply conflicting `passed` and `outcome` values. `category` is
`functional` (default), `visual`, or `performance`.

`session_finish(status="passed")` is a requested verdict, not an override:

- Any failed check yields `failed` (unless explicitly aborted).
- No checks, blocked/skipped/not-run checks, or failed requested evidence yield
  `incomplete`. This is deliberately conservative, including skipped scenarios.
- A caller-requested failed/aborted/incomplete result is never upgraded.
- JSON records the requested verdict, effective verdict, category counts and limits.
  Legacy Workspace history maps incomplete to cancelled, with the detailed
  `direct_status` retained.

An action returning `ok=true` only confirms action execution. It is not a business
assertion. A missing log entry is not proof that no error happened: Logcat remains
a bounded buffer snapshot, with `source_lines`, `possibly_truncated`, `since_time`
and an explicit coverage warning. Package filtering includes subprocesses found
in `ps -A` and historical process/crash records in the fetched buffer. Processes
whose identity is no longer present in the buffer may not be attributable.

## Recording

Inside an active session call `recording_start`, then perform actions and checks,
then call `recording_stop` or `session_finish`. Web Direct has the same recording
controls. scrcpy and FFmpeg must already be available on PATH.

Recordings reuse the controller's segmented recorder and preserve rotation/restart
gaps and the original video timing. Each recording is stored under `video-NNN/`.
`timeline.html` is an offline viewer: keep it alongside screenshots and video
folders when copying reports. Clicking an event time seeks the matching video
segment. This does not automatically classify flicker or extract every frame.

Event times denote action completion/evidence capture, not exact physical touch
onset. Segment alignment uses the existing recorder's first-frame estimate.
Normal MCP shutdown finalizes an active session as aborted and stops its recording.
Cancellation waits for an in-flight recording start/stop to finish so ownership
and finalization are not lost. Embedded `ActionSession` users should allow a
sufficient `close_timeout` for video conversion (the Web and case CLI use 60 seconds).
A process crash or forced OS termination cannot guarantee this cleanup.

## Semantic controls

- `click_element(selector, fallback_target?)`: exact AND matching on `text`,
  `resource_id`, `content_desc`, `package` and optional `enabled`. Uses a fresh
  hierarchy and current resolution. Duplicate matches fail. Explicit normalized
  coordinates are used only for no match, never on hierarchy errors or disabled
  targets.
- `wait_for_element(selector, state, timeout_ms, stable_ms)`: waits for `present`,
  `absent`, `enabled` or `stable`. Stable means a unique node's properties remain
  unchanged; it is not a pixel-stability assertion.
- `wait_for_app(package_name, timeout_ms)`: confirms foreground package during
  app/SDK handoffs. It does not assert the callback's business result.

Wait timeouts are bounded to 1–60000 ms. The hierarchy must expose accessible nodes;
canvas-only screens may still need image inspection and explicit coordinates.

## Executable JSON cases

`run_case(case)` owns a new session and requires no active session. All steps are
validated before execution. Only existing device actions and the semantic tools
are allowed; arbitrary shell/Python and nested sessions are not supported.
Set `assertion=true` only on `wait_for_element` or `wait_for_app`. At least one
assertion is required. Legacy `wait_for_text` is excluded because hierarchy failure
cannot safely establish disappearance.
On failure, subsequent actions are not executed and remaining assertions become
`not_run`. Include your precondition steps in each case so a rerun is independent.

Example schema (replace the text with a label verified on your target screen):

```json
{
  "name": "Current screen smoke",
  "parameters": {"package": "com.example.app", "label": "Home"},
  "timeout_ms": 30000,
  "record_video": false,
  "include_logcat": false,
  "steps": [
    {
      "name": "App is foreground",
      "tool": "wait_for_app",
      "arguments": {"package_name": "${package}", "timeout_ms": 5000},
      "assertion": true
    },
    {
      "name": "Home label visible",
      "tool": "wait_for_element",
      "arguments": {"selector": {"text": "${label}"}, "timeout_ms": 5000},
      "assertion": true
    }
  ]
}
```

PowerShell:

```powershell
uv run python -m artemis.mcp.direct_cases .\smoke.json --device "127.0.0.1:7555"
uv run python -m artemis.mcp.direct_cases .\smoke.json --device "127.0.0.1:7555" --param "label=首页"
```

A suite is `{"cases": [ ... ]}`. CLI writes `suite-results.json`; rerun failed
cases (including their preconditions) with `--failed-from .\suite-results.json`.
Each executed case exports `report.md`, `session.json`, `timeline.html`,
`case-results.json`, and `junit.xml`. Exit status is nonzero if any case does not pass.
Incomplete/failed evidence also produces a JUnit error even if individual steps pass.
Use unique case names in suites. Parameters are literal substitutions, not expressions.

Case actions run on the selected real device. Login, consent, SMS, binding and
network changes should only be included when explicitly authorized for that run.
These generic tools do not reset backend accounts or infer authorization.
