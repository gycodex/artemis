"""Regression tests for evidence that previously produced misleading passes."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from artemis.mcp.direct_server import _filter_logcat
from artemis.mcp.direct_session import DirectSession


def test_error_filter_preserves_threadtime_errors_and_fatal_lines():
    logs = (
        "10-20 12:00:00.000  123  123 E Network: request failed\n"
        "10-20 12:00:00.001  123  123 F libc: Fatal signal 11\n"
        "10-20 12:00:00.002  123  123 I Network: normal"
    )
    assert _filter_logcat(logs, None, True).splitlines() == logs.splitlines()[:2]


def test_package_filter_keeps_dead_subprocess_crash_stack():
    logs = (
        "10-20 12:00:00.000  100  100 I ActivityManager: "
        "Start proc 456:com.ronghui.ryb:pushservice/u0a51 for service\n"
        "10-20 12:00:01.000  456  456 E AndroidRuntime: FATAL EXCEPTION: main\n"
        "10-20 12:00:01.001  456  456 E AndroidRuntime: at Example.run(Example.java:1)\n"
        "10-20 12:00:01.002  789  789 E Other: unrelated"
    )
    result = _filter_logcat(logs, "com.ronghui.ryb", True, {"999"})
    assert "FATAL EXCEPTION" in result
    assert "Example.run" in result
    assert "unrelated" not in result


@pytest.fixture
def direct(tmp_path):
    with patch("artemis.mcp.direct_session.get_traces_dir", return_value=tmp_path):
        session = DirectSession(lambda: None, lambda: None, persist_history=False)
        session._capture = AsyncMock(return_value=(None, None))
        yield session


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome, expected", [("failed", "failed"), ("blocked", "incomplete"),
                          ("not_run", "incomplete"), ("skipped", "incomplete")]
)
async def test_finish_cannot_override_unmet_checks(direct, outcome, expected):
    await direct.start("honest results")
    await direct.checkpoint("required scenario", outcome=outcome, category="visual",
                            capture_screen=False)
    result = await direct.finish("passed", include_logcat=False)
    assert result.structuredContent["status"] == expected
    state = json.loads(Path(result.structuredContent["json_path"]).read_text("utf-8"))
    assert state["requested_status"] == "passed"
    assert state["checkpoints"][0]["outcome"] == outcome
    assert state["coverage"]["visual"][outcome] == 1
    assert outcome.upper() in Path(result.structuredContent["report_path"]).read_text("utf-8")


@pytest.mark.asyncio
async def test_empty_session_is_incomplete(direct):
    await direct.start("no assertions")
    result = await direct.finish(include_logcat=False)
    assert result.structuredContent["status"] == "incomplete"


@pytest.mark.asyncio
async def test_conflicting_legacy_and_new_checkpoint_values_rejected(direct):
    await direct.start("conflict")
    result = await direct.checkpoint("bad", passed=True, outcome="failed", capture_screen=False)
    assert result.structuredContent["ok"] is False
    assert direct.status().structuredContent["checkpoint_count"] == 0


@pytest.mark.asyncio
async def test_legacy_checkpoint_still_passes(direct):
    await direct.start("legacy")
    await direct.checkpoint("home", True, capture_screen=False)
    result = await direct.finish(include_logcat=False)
    assert result.structuredContent["status"] == "passed"


@pytest.mark.asyncio
async def test_diagnostic_failure_is_visible_in_report(direct):
    await direct.start("missing evidence")
    await direct.checkpoint("home", True, capture_screen=False)
    with patch("artemis.mcp.direct_session.fetch_and_filter_logs",
               side_effect=RuntimeError("device offline")):
        result = await direct.finish()
    report = Path(result.structuredContent["report_path"]).read_text("utf-8")
    assert "device offline" in report
    assert "incomplete" == result.structuredContent["status"]
