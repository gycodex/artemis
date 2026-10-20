# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""HTTP surface for model-free, browser-driven Direct testing."""

import asyncio
import json
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from artemis.config import TRACES_PATH

try:
    from admin_console.services.direct_control_service import (
        DIRECT_WEB_ACTIONS,
        direct_control_service,
    )
except ImportError:
    from apps.admin_console.services.direct_control_service import (
        DIRECT_WEB_ACTIONS,
        direct_control_service,
    )

try:
    from admin_console.database.repositories.session_repository import session_repo
except ImportError:
    from apps.admin_console.database.repositories.session_repository import session_repo

router = APIRouter(prefix="/api/direct", tags=["direct"])


class DirectStartRequest(BaseModel):
    device_serial: str = Field(min_length=1)
    name: str = Field(default="Web Direct mobile test", min_length=1)
    package_name: str | None = None
    auto_observe: bool = True


class DirectObserveRequest(BaseModel):
    settle_ms: int = Field(default=300, ge=0, le=5000)


class DirectActionRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class DirectCheckpointRequest(BaseModel):
    name: str = Field(min_length=1)
    passed: bool
    details: str = ""
    capture_screen: bool = True


class DirectFinishRequest(BaseModel):
    status: Literal["passed", "failed", "aborted"] = "passed"
    summary: str = ""
    include_logcat: bool = True
    logcat_lines: int = Field(default=1000, ge=1, le=5000)


def _http_error(exc: Exception) -> HTTPException:
    status = 409 if "busy" in str(exc).lower() or "already controlled" in str(exc).lower() else 400
    return HTTPException(status_code=status, detail=str(exc))


def _read_direct_artifacts(session_id: str) -> dict[str, Any]:
    row = session_repo.get_session_by_id(session_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    try:
        device_info = json.loads(row.get("device_info") or "{}")
    except (TypeError, ValueError):
        device_info = {}
    if device_info.get("profile") != "direct":
        raise HTTPException(status_code=404, detail="Direct session artifacts not found")

    traces_root = Path(TRACES_PATH).resolve()
    try:
        output_dir = Path(device_info["output_dir"]).resolve(strict=True)
    except (KeyError, OSError, ValueError):
        raise HTTPException(status_code=404, detail="Direct session output directory not found")
    if not output_dir.is_dir() or not output_dir.is_relative_to(traces_root):
        raise HTTPException(status_code=403, detail="Direct session output directory is invalid")

    state: dict[str, Any] = {}
    state_path = output_dir / "session.json"
    if state_path.is_file():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded
        except (OSError, ValueError):
            pass
    report_path = output_dir / "report.md"
    logcat_path = output_dir / "logcat.txt"
    report = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
    logcat = logcat_path.read_text(encoding="utf-8", errors="replace") if logcat_path.is_file() else ""
    screenshots = [
        {
            "name": path.name,
            "url": f"/local_file?path={quote(str(path))}",
        }
        for path in sorted((output_dir / "screenshots").glob("*.jpg"))
        if path.is_file()
    ]
    return {
        "session_id": session_id,
        "status": row.get("status"),
        "state": state,
        "report": report,
        "logcat_tail": logcat[-100_000:],
        "screenshots": screenshots,
    }


@router.get("/status")
async def direct_status():
    return await direct_control_service.status()


@router.get("/sessions/{session_id}/artifacts")
async def direct_session_artifacts(session_id: str):
    return await asyncio.to_thread(_read_direct_artifacts, session_id)


@router.post("/session/start")
async def direct_start(request: DirectStartRequest):
    try:
        return await direct_control_service.start(
            request.device_serial,
            request.name,
            request.package_name,
            request.auto_observe,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/observe")
async def direct_observe(request: DirectObserveRequest):
    try:
        return await direct_control_service.observe(request.settle_ms)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/action")
async def direct_action(request: DirectActionRequest):
    if request.tool not in DIRECT_WEB_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported Direct action: {request.tool}")
    try:
        return await direct_control_service.call(request.tool, request.arguments)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/checkpoint")
async def direct_checkpoint(request: DirectCheckpointRequest):
    try:
        return await direct_control_service.call("session_checkpoint", request.model_dump())
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/session/finish")
async def direct_finish(request: DirectFinishRequest):
    try:
        return await direct_control_service.finish(request.model_dump())
    except Exception as exc:
        raise _http_error(exc) from exc
