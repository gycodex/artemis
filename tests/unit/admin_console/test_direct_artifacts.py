"""Workspace artifact loading for completed Direct sessions."""

import json

import pytest
from fastapi import HTTPException

from apps.admin_console.routers import direct


class FakeRepository:
    def __init__(self, row):
        self.row = row

    def get_session_by_id(self, _session_id):
        return self.row


def test_read_direct_artifacts_is_bounded_to_trace_dir(tmp_path, monkeypatch):
    output_dir = tmp_path / "direct-test"
    screenshots = output_dir / "screenshots"
    screenshots.mkdir(parents=True)
    (screenshots / "001-home.jpg").write_bytes(b"jpeg")
    (output_dir / "report.md").write_text("# Report", encoding="utf-8")
    (output_dir / "logcat.txt").write_text("log line", encoding="utf-8")
    (output_dir / "session.json").write_text(
        json.dumps({"name": "Direct test", "actions": [], "checkpoints": []}),
        encoding="utf-8",
    )
    row = {
        "status": "completed",
        "device_info": json.dumps({"profile": "direct", "output_dir": str(output_dir)}),
    }
    monkeypatch.setattr(direct, "TRACES_PATH", tmp_path)
    monkeypatch.setattr(direct, "session_repo", FakeRepository(row))

    result = direct._read_direct_artifacts("direct-test")

    assert result["report"] == "# Report"
    assert result["logcat_tail"] == "log line"
    assert result["screenshots"][0]["name"] == "001-home.jpg"
    assert result["screenshots"][0]["url"].startswith("/local_file?path=")


def test_read_direct_artifacts_rejects_path_outside_trace_dir(tmp_path, monkeypatch):
    outside = tmp_path.parent / "outside-direct-test"
    outside.mkdir(exist_ok=True)
    row = {
        "status": "completed",
        "device_info": json.dumps({"profile": "direct", "output_dir": str(outside)}),
    }
    monkeypatch.setattr(direct, "TRACES_PATH", tmp_path)
    monkeypatch.setattr(direct, "session_repo", FakeRepository(row))

    with pytest.raises(HTTPException) as exc:
        direct._read_direct_artifacts("direct-test")

    assert exc.value.status_code == 403
