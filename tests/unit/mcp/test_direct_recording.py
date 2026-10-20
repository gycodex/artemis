import json
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from artemis.mcp.direct_session import DirectSession

pytestmark = pytest.mark.asyncio


@pytest.fixture
def direct(tmp_path):
    controller = SimpleNamespace(
        start_video_recording=AsyncMock(return_value=SimpleNamespace(
            success=True, message="started", video_id="test-video")),
        stop_video_recording=AsyncMock(return_value=SimpleNamespace(
            success=True, message="stopped", video_path=None)),
    )
    with patch("artemis.mcp.direct_session.get_traces_dir", return_value=tmp_path):
        session = DirectSession(lambda: None, lambda: controller, persist_history=False)
        session._capture = AsyncMock(return_value=(None, None))
        yield session, controller


async def test_recording_requires_active_session(direct):
    session, controller = direct
    result = await session.recording_start()
    assert result.structuredContent["ok"] is False
    controller.start_video_recording.assert_not_awaited()


async def test_finish_stops_recording_and_exports_timeline(direct):
    session, controller = direct
    started = await session.start("timeline <script>alert(1)</script>")
    await session.recording_start()
    await session.checkpoint("home", True, capture_screen=False)
    result = await session.finish(include_logcat=False)
    controller.stop_video_recording.assert_awaited_once()
    state = json.loads(Path(result.structuredContent["json_path"]).read_text("utf-8"))
    assert state["recordings"][0]["status"] == "stopped"
    assert state["checkpoints"][0]["offset_ms"] >= 0
    timeline = Path(result.structuredContent["timeline_path"]).read_text("utf-8")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in timeline
    assert "home" in timeline
    assert Path(started.structuredContent["output_dir"], "timeline.html").exists()


async def test_recording_failure_is_preserved_and_not_passed(direct):
    session, controller = direct
    controller.start_video_recording.return_value = SimpleNamespace(
        success=False, message="scrcpy missing", video_id=None)
    await session.start("recording failed")
    result = await session.recording_start()
    assert result.structuredContent["ok"] is False
    await session.checkpoint("home", True, capture_screen=False)
    finished = await session.finish(include_logcat=False)
    assert finished.structuredContent["status"] == "incomplete"
    assert "scrcpy missing" in Path(finished.structuredContent["report_path"]).read_text("utf-8")


async def test_close_aborts_unfinished_session_and_stops_owned_recording(direct):
    session, controller = direct
    await session.start("disconnect")
    await session.recording_start()
    await session.close()
    controller.stop_video_recording.assert_awaited_once()
    assert not session.active
    report = Path(session.status().structuredContent["last_report"]).read_text("utf-8")
    assert "**aborted**" in report


async def test_duplicate_recording_start_does_not_restart_controller(direct):
    session, controller = direct
    await session.start("duplicate")
    await session.recording_start()
    result = await session.recording_start()
    assert result.structuredContent["ok"] is False
    controller.start_video_recording.assert_awaited_once()
    await session.close()


@pytest.mark.parametrize("operation", ["start", "stop"])
async def test_cancelled_recording_operation_completes_before_close(direct, operation):
    session, controller = direct
    await session.start("cancel lifecycle")
    entered = asyncio.Event()
    released = asyncio.Event()
    original = getattr(controller, f"{operation}_video_recording")

    async def delayed(**kwargs):
        entered.set()
        await released.wait()
        return await original(**kwargs)

    if operation == "stop":
        await session.recording_start()
    setattr(controller, f"{operation}_video_recording", delayed)
    task = asyncio.create_task(getattr(session, f"recording_{operation}")())
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await session.close()
    assert session._recording is None
    original.assert_awaited_once()
    state = json.loads(Path(session.status().structuredContent["last_report"]).with_name(
        "session.json").read_text("utf-8"))
    assert state["recordings"][0]["status"] == "stopped"


async def test_stop_does_not_stop_a_replacement_recording(direct):
    session, controller = direct
    await session.start("ownership")
    await session.recording_start()
    with patch("third_party.mobile_use.utils.video.get_active_session",
               return_value=SimpleNamespace(video_id="different-recording")):
        result = await session.recording_stop()
    assert result.structuredContent["ok"] is False
    controller.stop_video_recording.assert_not_awaited()
    await session.close()


async def test_transport_shutdown_awaits_recording_cleanup(tmp_path):
    import asyncio
    from artemis.mcp.action_session import ActionSession
    from artemis.mcp.actuators import MockActuator
    from artemis.mcp.direct_server import build_direct_server
    actuator = MockActuator()
    completed = []
    actuator.controller.start_video_recording = AsyncMock(
        return_value=SimpleNamespace(success=True, message="started", video_id="owned"))

    async def stop():
        await asyncio.sleep(0.1)
        completed.append(True)
        return SimpleNamespace(success=True, message="stopped", video_path=None)

    actuator.controller.stop_video_recording = stop
    client = await ActionSession(build_direct_server(actuator, persist_history=False)).start()
    with patch("artemis.mcp.direct_session.get_traces_dir", return_value=tmp_path):
        started = await client.call_raw("session_start", {"name": "transport exit"})
        await client.call_raw("recording_start", {})
        await client.aclose()
    assert completed == [True]
    root = Path(started.structuredContent["output_dir"])
    assert json.loads((root / "session.json").read_text("utf-8"))["status"] == "aborted"
    assert (root / "report.md").is_file()
