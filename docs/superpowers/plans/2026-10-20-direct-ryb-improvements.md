# Direct RYB Improvements Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve the reliability and repeatability of the existing RYB Direct workflow.

**Architecture:** Keep the existing action surface compatible. Add diagnostics, recording, selectors and a declarative regression runner around DirectSession; reuse the controller's existing recording implementation.

**Tech Stack:** Python, FastMCP, pytest, existing ADB/scrcpy controller.

**Spec:** User-approved recommendations in this conversation: diagnostics and verdicts first, recording timeline second, semantic selectors and executable regression cases third.

## Global Constraints

- Preserve legacy boolean checkpoint calls.
- No model credentials required; no live account mutation during verification.
- New case files contain explicit actions and assertions, not executable Python or shell.
- Existing user changes and historical traces remain untouched.
- Work in `codex/direct-ryb-improvements`; validate with the existing Python environment.

## Review Focus

- Error filtering must retain threadtime E/F lines and crash context from old/subprocess PIDs.
- A failed or unexecuted check must not become an overall pass.
- Recording failure must preserve reports, and shutdown must stop only owned recording.
- Ambiguous selectors and hierarchy failures must not silently click a coordinate.
- Invalid regression steps must fail validation before any device action.

### Task 1: Diagnostics and verdicts

Files: direct_server.py, direct_session.py, Direct HTTP schemas/repository, tests.

- [x] Add failing tests for log formats, historical subprocesses, incomplete checks and inconsistent verdicts.
- [x] Implement validated package filters, explicit evidence coverage, five checkpoint outcomes and categories.
- [x] Keep old calls compatible; expose incomplete sessions as cancelled in legacy history.
- [x] Run targeted MCP/admin tests and commit (`3861cbe`).

### Task 2: Recording and timeline

Files: direct_session.py, direct_server.py, direct_timeline.py, tests.

- [x] Add failing tests for recording lifecycle, failure evidence and timeline export.
- [x] Reuse controller recording with session-local output and first-frame anchor; automatically finalize on session finish/shutdown.
- [x] Export a standalone HTML timeline linking screenshots, actions and segmented videos.
- [x] Run targeted tests and commit (`e132f8b`; reviewed lifecycle fixes included in Task 3).

### Task 3: Selectors and regression cases

Files: direct_selectors.py, direct_cases.py, direct_server.py, tests, usage documentation.

- [x] Add failing tests for unique/ambiguous/missing selectors, waits, validation before execution and fail-fast cases.
- [x] Expose selector clicks and waits using fresh hierarchy; allow explicit coordinate fallback only when no match.
- [x] Add JSON case runner with typed steps, parameter substitution, checkpoints and JUnit output.
- [x] Verify via mock-device integration and inspect live environment availability (limitations below).
- [x] Run scoped regression checks and independent review; commit with this completed plan.

## Execution Notes

- Baseline: 13 Direct MCP/admin tests passed in original checkout at 4a35701.
- Ruling: follow the user's explicit implementation instruction without an additional design approval round.
- Ruling: reuse the controller's segmented scrcpy recorder, not the older standalone ScreenRecorder.
- Independent read-only review found seven issues: string bounds, unavailable hierarchy,
  failed evidence verdicts, action preflight, transport shutdown, unsafe legacy wait
  assertions, and recording lifecycle cancellation. All reproduced and addressed
  with regression tests; replacement-recording ownership is also checked.
- Final Python verification: 316 passed, 1 skipped across MCP, Direct admin,
  drivers, hierarchy fidelity and controller recording tests. The isolated worktree
  needed an empty history database for a legacy trace-inspection test; original
  history was not copied or changed. A concurrent-build handshake timeout passed
  on isolated rerun and in the final suite.
- Frontend: 133 ChromeHeadless tests passed; Angular production build passed.
  Ruff checks and Git whitespace checks passed.
- Live limits: Direct MCP status/device calls timed out. Local ADB listed two
  devices, neither at the originally requested 127.0.0.1:7555 endpoint.
  Doctor reported scrcpy missing. No RYB login/consent/account state was changed;
  real-device recording, rotation/playback and RYB-specific regression paths
  still require an available selected device and recording dependencies.
- Integration ruling: the user's earlier explicit "确认调整好，就推送"
  authorizes committing and pushing this adjustment batch to the existing fork.
  No official-upstream push or new PR is requested.
