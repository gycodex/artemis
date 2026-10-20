# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Contract tests for the no-LLM, host-driven MCP surface."""

import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock, patch

import pytest

from artemis.mcp.action_session import ActionSession
from artemis.mcp.actuators import MockActuator
from artemis.mcp.direct_server import build_direct_adb_server, build_direct_server

pytestmark = pytest.mark.asyncio


async def test_direct_server_exposes_actions_observation_and_diagnostics():
    session = await ActionSession(
        build_direct_server(MockActuator(), persist_history=False)
    ).start()
    try:
        names = {tool.name for tool in (await session._session.list_tools()).tools}
        assert {
            "click",
            "observe_screen",
            "list_devices",
            "get_device_info",
            "read_logcat",
            "session_start",
            "session_checkpoint",
            "session_status",
            "session_finish",
        } <= names
    finally:
        await session.aclose()


async def test_direct_adb_server_does_not_touch_device_during_mcp_startup():
    with patch("artemis.mcp.adb_server._get_controller") as get_controller:
        server = build_direct_adb_server("offline-device")
        names = {tool.name for tool in await server.list_tools()}
    assert "observe_screen" in names
    get_controller.assert_not_called()


async def test_direct_server_reads_bounded_filtered_logcat():
    actuator = MockActuator()
    device = Mock()
    device.shell.return_value = "1234"
    session = await ActionSession(build_direct_server(actuator, persist_history=False)).start()
    try:
        with (
            patch("artemis.mcp.direct_server.get_adb_device", return_value=device),
            patch("artemis.mcp.direct_server.fetch_and_filter_logs") as fetch,
        ):
            fetch.return_value = (
                "09-18 10:00:00.000  1234  1234 E AndroidRuntime: fatal exception\n"
                "09-18 10:00:00.100  9999  9999 I Other: routine message"
            )
            raw = await session.call_raw(
                "read_logcat",
                {"lines": 99999, "package_name": "com.example.app", "errors_only": True},
            )
        assert raw.structuredContent["ok"] is True
        assert raw.structuredContent["requested_lines"] == 5000
        assert raw.structuredContent["returned_lines"] == 1
        assert "fatal exception" in raw.content[0].text
        assert "routine message" not in raw.content[0].text
    finally:
        await session.aclose()


async def test_direct_server_device_info_is_non_fatal_without_adb_metadata():
    actuator = MockActuator(width=720, height=1280)
    session = await ActionSession(build_direct_server(actuator, persist_history=False)).start()
    try:
        with patch("artemis.mcp.direct_server.get_adb_device", return_value=None):
            raw = await session.call_raw("get_device_info", {})
        assert raw.structuredContent["ok"] is True
        assert raw.structuredContent["width"] == 720
        assert raw.structuredContent["height"] == 1280
    finally:
        await session.aclose()


async def test_direct_session_records_actions_checkpoints_and_reports(tmp_path: Path):
    session = await ActionSession(
        build_direct_server(MockActuator(), persist_history=False)
    ).start()
    try:
        with patch("artemis.mcp.direct_session.get_traces_dir", return_value=tmp_path):
            started = await session.call_raw(
                "session_start",
                {
                    "name": "Launch smoke test",
                    "package_name": "com.example.app",
                    "auto_observe": True,
                },
            )
            output_dir = Path(started.structuredContent["output_dir"])

            action = await session.call_raw("click", {"target": [500, 500]})
            assert action.structuredContent["ok"] is True
            assert any(block.type == "image" for block in action.content)

            checkpoint = await session.call_raw(
                "session_checkpoint",
                {
                    "name": "home visible",
                    "passed": True,
                    "details": "Expected controls are present.",
                },
            )
            assert checkpoint.structuredContent["checkpoint"]["passed"] is True

            status = await session.call_raw("session_status", {})
            assert status.structuredContent["active"] is True
            assert status.structuredContent["action_count"] == 1
            assert status.structuredContent["checkpoint_count"] == 1

            with patch(
                "artemis.mcp.direct_session.fetch_and_filter_logs",
                return_value="sample logcat",
            ):
                finished = await session.call_raw(
                    "session_finish",
                    {"status": "passed", "summary": "All checks passed."},
                )

        report_path = Path(finished.structuredContent["report_path"])
        state_path = Path(finished.structuredContent["json_path"])
        assert report_path.is_file()
        assert state_path.is_file()
        assert (output_dir / "logcat.txt").read_text(encoding="utf-8") == "sample logcat"
        assert "All checks passed." in report_path.read_text(encoding="utf-8")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["status"] == "passed"
        assert len(state["actions"]) == 1
        assert len(state["checkpoints"]) == 1
        assert state["artifacts"]["final_observation"]["image_name"] == "003-final"

        ended = await session.call_raw("session_status", {})
        assert ended.structuredContent["active"] is False
        assert ended.structuredContent["last_report"] == str(report_path)
    finally:
        await session.aclose()


async def test_direct_session_can_record_without_automatic_observation(tmp_path: Path):
    session = await ActionSession(
        build_direct_server(MockActuator(), persist_history=False)
    ).start()
    try:
        with patch("artemis.mcp.direct_session.get_traces_dir", return_value=tmp_path):
            started = await session.call_raw(
                "session_start",
                {"name": "Fast actions", "auto_observe": False},
            )
            action = await session.call_raw("click", {"target": [100, 100]})
            assert not any(block.type == "image" for block in action.content)
            observed = await session.call_raw(
                "observe_screen", {"settle_ms": 0, "include_image": True}
            )
            archived_path = Path(observed.structuredContent["screenshot_path"])
            assert archived_path.is_file()
            assert archived_path.is_relative_to(Path(started.structuredContent["output_dir"]))
            status = await session.call_raw("session_status", {})
            assert status.structuredContent["action_count"] == 1
            await session.call_raw(
                "session_finish",
                {"status": "aborted", "include_logcat": False},
            )
            state = json.loads(
                (Path(started.structuredContent["output_dir"]) / "session.json").read_text(
                    encoding="utf-8"
                )
            )
            assert len(state["observations"]) == 1
    finally:
        await session.aclose()


async def test_mcp_direct_session_persists_in_workspace_history(tmp_path: Path):
    db_path = tmp_path / "history.db"
    session = await ActionSession(
        build_direct_server(MockActuator(), ingress="mcp-direct", persist_history=True)
    ).start()
    try:
        with (
            patch("artemis.mcp.direct_session.get_traces_dir", return_value=tmp_path),
            patch("artemis.mcp.direct_session.get_data_engine_db_path", return_value=db_path),
        ):
            started = await session.call_raw(
                "session_start", {"name": "MCP persisted test", "auto_observe": False}
            )
            await session.call_raw(
                "session_finish", {"status": "passed", "include_logcat": False}
            )

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT status, device_info FROM sessions WHERE session_id = ?",
                (started.structuredContent["session_id"],),
            ).fetchone()
        assert row is not None
        assert row[0] == "completed"
        assert json.loads(row[1])["ingress"] == "mcp-direct"
    finally:
        await session.aclose()
