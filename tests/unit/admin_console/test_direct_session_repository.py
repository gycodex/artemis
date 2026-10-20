"""Direct sessions share the normal Workspace history database."""

import json
import sqlite3

from apps.admin_console.database.repositories.session_repository import SessionRepository


def _create_sessions_table(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE sessions ("
            "session_id TEXT PRIMARY KEY, initial_goal TEXT, start_time REAL, end_time REAL, "
            "status TEXT, device_info TEXT, pid INTEGER, video_filepath TEXT)"
        )


def test_direct_session_lifecycle_is_persisted(tmp_path):
    db_path = tmp_path / "history.db"
    _create_sessions_table(db_path)
    repository = SessionRepository(db_path)

    repository.create_direct_session(
        "direct-test",
        "Privacy smoke test",
        "127.0.0.1:7555",
        "com.ronghui.ryb",
        str(tmp_path / "direct-test"),
        True,
    )
    running = repository.get_session_by_id("direct-test")
    assert running is not None
    assert running["status"] == "running"
    info = json.loads(running["device_info"])
    assert info["profile"] == "direct"
    assert info["device_serial"] == "127.0.0.1:7555"

    assert repository.finish_direct_session(
        "direct-test", "passed", "report.md", "session.json"
    )
    finished = repository.get_session_by_id("direct-test")
    assert finished is not None
    assert finished["status"] == "completed"
    assert finished["end_time"] is not None
    assert finished["pid"] is None
    artifacts = json.loads(finished["device_info"])["artifacts"]
    assert artifacts == {"report_path": "report.md", "json_path": "session.json"}


def test_sync_direct_sessions_backfills_older_mcp_artifacts(tmp_path):
    db_path = tmp_path / "history.db"
    _create_sessions_table(db_path)
    output_dir = tmp_path / "direct-old"
    output_dir.mkdir()
    (output_dir / "session.json").write_text(
        json.dumps(
            {
                "session_id": "direct-old",
                "name": "Older MCP test",
                "package_name": "com.example",
                "status": "passed",
                "started_at": "2026-09-18T23:36:59+08:00",
                "auto_observe": False,
            }
        ),
        encoding="utf-8",
    )
    repository = SessionRepository(db_path)

    assert repository.sync_direct_sessions(tmp_path) == 1
    assert repository.sync_direct_sessions(tmp_path) == 0
    row = repository.get_session_by_id("direct-old")
    assert row is not None
    assert row["status"] == "completed"
    info = json.loads(row["device_info"])
    assert info["profile"] == "direct"
    assert info["ingress"] == "mcp-direct"
