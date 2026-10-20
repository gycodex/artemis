"""Tests for the model-free Web Direct control service."""

from types import SimpleNamespace

from mcp.types import CallToolResult, ImageContent, TextContent
import pytest

from apps.admin_console.services import direct_control_service as module

pytestmark = pytest.mark.asyncio


def _result(payload: dict, image: bool = False) -> CallToolResult:
    content = [TextContent(type="text", text="done")]
    if image:
        content.append(ImageContent(type="image", data="aW1hZ2U=", mimeType="image/jpeg"))
    return CallToolResult(content=content, structuredContent=payload, isError=False)


class FakeActionSession:
    def __init__(self, _server, *, close_timeout=5.0):
        self.started = False
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    async def start(self):
        self.started = True
        return self

    async def call_raw(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        if name == "session_start":
            return _result(
                {
                    "ok": True,
                    "active": True,
                    "session_id": "direct-test",
                    "output_dir": "trace/direct-test",
                }
            )
        if name == "session_status":
            return _result({"ok": True, "active": True, "action_count": 1})
        if name == "observe_screen":
            return _result({"ok": True, "message": "Screen observed."}, image=True)
        if name == "session_finish":
            return _result({"ok": True, "report_path": "trace/report.md"}, image=True)
        return _result({"ok": True, "action": name}, image=True)

    async def aclose(self):
        self.started = False
        self.closed = True


class FakeDeviceLock:
    instances: list["FakeDeviceLock"] = []

    def __init__(self, device_id: str, *_args, **_kwargs):
        self.device_id = device_id
        self.acquired = False
        self.released = False
        self.instances.append(self)

    def acquire(self, **_kwargs):
        self.acquired = True

    def release(self):
        self.released = True


class FakeSessionRepository:
    def __init__(self):
        self.created: list[tuple] = []
        self.finished: list[tuple] = []
        self.statuses: dict[str, str] = {}

    def create_direct_session(self, *args):
        self.created.append(args)
        self.statuses[str(args[0])] = "running"

    def finish_direct_session(self, *args):
        self.finished.append(args)
        self.statuses[str(args[0])] = "completed"
        return True

    def get_session_status(self, session_id: str):
        return self.statuses.get(session_id)


async def test_direct_web_session_reuses_mcp_tools_and_releases_device(monkeypatch):
    FakeDeviceLock.instances.clear()
    monkeypatch.setattr(module, "ActionSession", FakeActionSession)
    monkeypatch.setattr(module, "DeviceExecutionLock", FakeDeviceLock)
    fake_repo = FakeSessionRepository()
    monkeypatch.setattr(module, "session_repo", fake_repo)
    monkeypatch.setattr(
        module,
        "build_direct_adb_server",
        lambda serial, **kwargs: SimpleNamespace(serial=serial, **kwargs),
    )
    service = module.DirectControlService()

    started = await service.start("emulator-5554", "Smoke", "com.example", True)
    assert started["result"]["ok"] is True
    assert started["device_serial"] == "emulator-5554"
    assert FakeDeviceLock.instances[0].acquired is True
    assert fake_repo.created[0][:4] == (
        "direct-test",
        "Smoke",
        "emulator-5554",
        "com.example",
    )

    observed = await service.observe(0)
    assert observed["image_data_url"] == "data:image/jpeg;base64,aW1hZ2U="

    action = await service.call("press_key", {"key": "home"})
    assert action["result"]["action"] == "press_key"

    finished = await service.finish(
        {"status": "passed", "summary": "ok", "include_logcat": False, "logcat_lines": 10}
    )
    assert finished["result"]["report_path"] == "trace/report.md"
    assert service.active is False
    assert FakeDeviceLock.instances[0].released is True
    assert fake_repo.finished[0][0:2] == ("direct-test", "passed")


async def test_direct_web_status_is_inactive_before_start(monkeypatch):
    monkeypatch.setattr(module, "session_repo", FakeSessionRepository())
    service = module.DirectControlService()
    status = await service.status()
    assert status["result"] == {"ok": True, "active": False}
