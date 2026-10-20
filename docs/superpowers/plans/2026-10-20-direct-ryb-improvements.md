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

Files: direct_server.py, direct_session.py, direct_logs.py, Direct HTTP schemas/repository, tests.

- [ ] Add failing tests for log formats, historical subprocesses, incomplete checks and inconsistent verdicts.
- [ ] Implement validated package filters, explicit evidence coverage, five checkpoint outcomes and categories.
- [ ] Keep old calls compatible; expose incomplete sessions as cancelled in legacy history.
- [ ] Run targeted MCP/admin tests and commit.

### Task 2: Recording and timeline

Files: direct_session.py, direct_server.py, direct_recording.py, tests.

- [ ] Add failing tests for recording lifecycle, failure evidence and timeline export.
- [ ] Reuse controller recording with session-local output and first-frame anchor; automatically finalize on session finish/shutdown.
- [ ] Export a standalone HTML timeline linking screenshots, actions and segmented videos.
- [ ] Run targeted tests and commit.

### Task 3: Selectors and regression cases

Files: direct_selectors.py, direct_cases.py, direct_server.py, tests, usage documentation.

- [ ] Add failing tests for unique/ambiguous/missing selectors, waits, validation before execution and fail-fast cases.
- [ ] Expose selector clicks and waits using fresh hierarchy; allow explicit coordinate fallback only when no match.
- [ ] Add JSON case runner with typed steps, parameter substitution, checkpoints and JUnit output.
- [ ] Verify via mock-device integration and a read-only live device probe if available.
- [ ] Run scoped regression checks, review diff and commit.

## Execution Notes

- Baseline: 13 Direct MCP/admin tests passed in original checkout at 4a35701.
- Ruling: follow the user's explicit implementation instruction without an additional design approval round.
- Ruling: reuse the controller's segmented scrcpy recorder, not the older standalone ScreenRecorder.
