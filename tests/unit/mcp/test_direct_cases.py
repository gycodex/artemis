import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from artemis.mcp.action_session import ActionSession
from artemis.mcp.actuators import MockActuator
from artemis.mcp.direct_server import build_direct_server

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def client_for(actuator):
    client = await ActionSession(build_direct_server(actuator, persist_history=False)).start()
    try:
        yield client
    finally:
        await client.aclose()


async def test_case_executes_wait_assertion_and_exports_junit(tmp_path):
    actuator = MockActuator()
    async with client_for(actuator) as client:
        with patch("artemis.mcp.direct_session.get_traces_dir", return_value=tmp_path):
            result = await client.call_raw("run_case", {"case": {
                "name": "Settings smoke", "parameters": {"label": "Settings"},
                "steps": [
                    {"name": "tap settings", "tool": "click_element",
                     "arguments": {"selector": {"text": "${label}"}}},
                    {"name": "settings visible", "tool": "wait_for_element",
                     "arguments": {"selector": {"text": "${label}"}, "timeout_ms": 100},
                     "assertion": True},
                ],
            }})
    assert result.structuredContent["status"] == "passed"
    assert len(actuator.action_history) == 1
    tree = ET.parse(result.structuredContent["junit_path"])
    assert tree.getroot().get("failures") == "0"
    assert tree.getroot().get("tests") == "2"


async def test_case_prevalidates_later_invalid_steps_before_any_click(tmp_path):
    actuator = MockActuator()
    async with client_for(actuator) as client:
        result = await client.call_raw("run_case", {"case": {
            "name": "invalid",
            "steps": [
                {"name": "first", "tool": "click", "arguments": {"target": [500, 500]}},
                {"name": "bad", "tool": "execute_shell", "arguments": {"command": "unsafe"}},
                {"name": "check", "tool": "wait_for_app",
                 "arguments": {"package_name": "com.android.settings"}, "assertion": True},
            ],
        }})
    assert result.structuredContent["ok"] is False
    assert not actuator.action_history


async def test_case_failure_skips_remaining_actions_and_marks_missing_checks(tmp_path):
    actuator = MockActuator()
    async with client_for(actuator) as client:
        with patch("artemis.mcp.direct_session.get_traces_dir", return_value=tmp_path):
            result = await client.call_raw("run_case", {"case": {
                "name": "fail fast", "steps": [
                    {"name": "missing", "tool": "wait_for_element",
                     "arguments": {"selector": {"text": "not here"}, "timeout_ms": 10},
                     "assertion": True},
                    {"name": "must not tap", "tool": "click", "arguments": {"target": [500, 500]}},
                    {"name": "not verified", "tool": "wait_for_app",
                     "arguments": {"package_name": "com.android.settings"},
                     "assertion": True},
                ],
            }})
    assert result.structuredContent["status"] == "failed"
    assert not actuator.action_history
    state = json.loads(Path(result.structuredContent["json_path"]).read_text("utf-8"))
    assert [c["outcome"] for c in state["checkpoints"]] == ["failed", "not_run"]
    root = ET.parse(result.structuredContent["junit_path"]).getroot()
    assert root.get("skipped") == "2"


async def test_case_rejects_missing_parameter_and_invalid_action_arguments():
    actuator = MockActuator()
    async with client_for(actuator) as client:
        for arguments in ({"target": "${missing}"}, {"wrong": [500, 500]}):
            result = await client.call_raw("run_case", {"case": {
                "name": "bad args",
                "steps": [
                    {"name": "bad", "tool": "click", "arguments": arguments},
                    {"name": "check", "tool": "wait_for_app",
                     "arguments": {"package_name": "com.android.settings"}, "assertion": True},
                ],
            }})
            assert result.structuredContent["ok"] is False
    assert not actuator.action_history


async def test_negative_wait_timeout_is_rejected_before_first_action():
    actuator = MockActuator()
    async with client_for(actuator) as client:
        result = await client.call_raw("run_case", {"case": {
            "name": "preflight bounds", "steps": [
                {"name": "tap", "tool": "click", "arguments": {"target": [500, 500]}},
                {"name": "invalid wait", "tool": "wait_for_element",
                 "arguments": {"selector": {"text": "Settings"}, "timeout_ms": -1},
                 "assertion": True},
            ],
        }})
    assert result.structuredContent["ok"] is False
    assert not actuator.action_history


async def test_recording_failure_produces_junit_error(tmp_path):
    actuator = MockActuator()
    actuator.controller.start_video_recording = AsyncMock(
        return_value=SimpleNamespace(success=False, message="scrcpy missing"))
    async with client_for(actuator) as client:
        with patch("artemis.mcp.direct_session.get_traces_dir", return_value=tmp_path):
            result = await client.call_raw("run_case", {"case": {
                "name": "recording required", "record_video": True,
                "steps": [{"name": "foreground", "tool": "wait_for_app",
                           "arguments": {"package_name": "com.android.settings"},
                           "assertion": True}],
            }})
    root = ET.parse(result.structuredContent["junit_path"]).getroot()
    assert int(root.get("errors", "0")) > 0


async def test_concurrent_case_calls_cannot_share_session(tmp_path):
    import asyncio
    actuator = MockActuator()
    server = build_direct_server(actuator, persist_history=False)
    case = {"name": "concurrent", "steps": [
        {"name": "ready", "tool": "wait_for_app",
         "arguments": {"package_name": "com.android.settings"}, "assertion": True}
    ]}
    with patch("artemis.mcp.direct_session.get_traces_dir", return_value=tmp_path):
        # Invoke registered handlers as concurrent MCP requests would.
        tool = server._tool_manager.get_tool("run_case")
        results = await asyncio.gather(tool.run({"case": case}), tool.run({"case": case}))
    assert all(result.structuredContent.get("status") == "passed" for result in results)
    assert len({r.structuredContent["session_id"] for r in results}) == 2


@pytest.mark.parametrize("tool,args", [
    ("click", {"target": [500]}),
    ("click_sequence", {"sequence": [[500, 500], [1]]}),
    ("click_element", {"selector": {"text": "Settings"}, "fallback_target": [-1, 0]}),
    ("wait_for_text", {"text": "x", "wait_state": "typo"}),
    ("manage_app", {"action": "clear", "app_name": "com.example.app"}),
])
async def test_case_rejects_semantically_invalid_later_step(tool, args):
    actuator = MockActuator()
    async with client_for(actuator) as client:
        result = await client.call_raw("run_case", {"case": {
            "name": "preflight", "steps": [
                {"name": "first", "tool": "click", "arguments": {"target": [500, 500]}},
                {"name": "invalid", "tool": tool, "arguments": args},
                {"name": "check", "tool": "wait_for_app",
                 "arguments": {"package_name": "com.android.settings"}, "assertion": True},
            ],
        }})
    assert result.structuredContent["ok"] is False
    assert not actuator.action_history
