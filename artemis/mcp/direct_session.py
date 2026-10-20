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

"""Persistent, model-free test sessions for the Direct MCP server."""

import asyncio
import base64
from collections.abc import Callable
from datetime import datetime
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Literal
import uuid

import anyio
from mcp.types import CallToolResult, ImageContent, TextContent

from artemis.config.paths import get_data_engine_db_path, get_traces_dir
from artemis.data_engine.models import SessionMetadata
from artemis.data_engine.storage import StorageManager
from artemis.mcp.action_types import ActionResult, ObserveResult
from artemis.mcp.observation import observe
from artemis.mcp.direct_timeline import write_timeline
from artemis.tools.mobile.log_utils import fetch_and_filter_logs

__all__ = ["DirectSession"]


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    return cleaned[:60] or "session"


class DirectSession:
    """Records host-driven actions, screenshots, assertions, logs, and reports."""

    def __init__(
        self,
        context_factory: Callable[[], Any],
        controller_factory: Callable[[], Any],
        *,
        device_serial: str | None = None,
        ingress: str = "mcp-direct",
        persist_history: bool = True,
    ):
        self._context_factory = context_factory
        self._controller_factory = controller_factory
        self._device_serial = device_serial
        self._ingress = ingress
        self._persist_history = persist_history
        self._state: dict[str, Any] | None = None
        self._last_report: str | None = None
        self._recording: dict[str, Any] | None = None

    def _offset_ms(self) -> int:
        return max(0, int((time.time() - self._state["started_at_epoch"]) * 1000))

    async def recording_start(self) -> CallToolResult:
        return await self._finish_recording_operation(self._recording_start())

    @staticmethod
    async def _finish_recording_operation(operation) -> CallToolResult:
        """Do not lose recorder ownership when a caller cancels mid-operation."""
        cancelled = False
        with anyio.CancelScope(shield=True):
            task = asyncio.create_task(operation)
            while True:
                try:
                    result = await asyncio.shield(task)
                    break
                except asyncio.CancelledError:
                    if task.cancelled():
                        raise
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError
            return result

    async def _recording_start(self) -> CallToolResult:
        """Start an owned recording, reusing controller rotation/segment handling."""
        if not self._state or self._recording:
            return self._result("Start a session first; only one recording may be active.",
                                {"ok": False})
        output = Path(self._state["output_dir"]) / f"video-{len(self._state['recordings']) + 1:03d}"
        record = {"output_dir": str(output), "offset_ms": self._offset_ms(), "status": "starting"}
        self._state["recordings"].append(record)
        try:
            controller = self._controller_factory()
            result = await controller.start_video_recording(output_dir=output)
            if not result.success:
                raise RuntimeError(result.message)
            self._recording = record
            record["status"] = "recording"
            record["video_id"] = str(getattr(result, "video_id", "") or "")
            # Make the existing recording manifest use the Direct session clock.
            from third_party.mobile_use.utils.video import get_active_session
            device_id = getattr(getattr(self._context_factory(), "device", None), "device_id", "")
            active = get_active_session(device_id)
            if active is not None:
                active.data_engine_start_time = self._state["started_at_epoch"]
                record["offset_ms"] = max(
                    0, int((active.start_time - self._state["started_at_epoch"]) * 1000)
                )
            self._write_state()
            return self._result("Recording started.", {"ok": True, "recording": record})
        except Exception as exc:
            record.update(status="failed", error=str(exc))
            self._state["artifacts"]["recording_error"] = str(exc)
            self._write_state()
            return self._result(str(exc), {"ok": False, "recording": record})

    async def recording_stop(self) -> CallToolResult:
        return await self._finish_recording_operation(self._recording_stop())

    async def _recording_stop(self) -> CallToolResult:
        if not self._state or self._recording is None:
            return self._result("No owned recording is active.", {"ok": False})
        record = self._recording
        try:
            from third_party.mobile_use.utils.video import get_active_session
            device_id = getattr(getattr(self._context_factory(), "device", None), "device_id", "")
            active = get_active_session(device_id)
            if active is not None and str(active.video_id) != record.get("video_id"):
                raise RuntimeError("Recording ownership changed; refusing to stop another recording.")
            result = await self._controller_factory().stop_video_recording()
            if not result.success:
                raise RuntimeError(result.message)
            record.update(status="stopped", end_offset_ms=self._offset_ms())
            if result.video_path:
                record["video_path"] = str(result.video_path)
            return self._result("Recording stopped.", {"ok": True, "recording": record})
        except Exception as exc:
            record.update(status="failed", error=str(exc))
            self._state["artifacts"]["recording_error"] = str(exc)
            return self._result(str(exc), {"ok": False, "recording": record})
        finally:
            self._recording = None
            self._write_state()

    async def close(self) -> None:
        """Finalize owned evidence when the MCP transport exits normally."""
        if self.active:
            await self.finish("aborted", "MCP connection closed before session_finish.",
                              include_logcat=False)

    @property
    def active(self) -> bool:
        return self._state is not None

    def _result(
        self,
        message: str,
        payload: dict[str, Any],
        image_bytes: bytes | None = None,
    ) -> CallToolResult:
        content: list[Any] = [TextContent(type="text", text=message)]
        if image_bytes:
            content.append(
                ImageContent(
                    type="image",
                    data=base64.b64encode(image_bytes).decode("utf-8"),
                    mimeType="image/jpeg",
                )
            )
        return CallToolResult(content=content, structuredContent=payload, isError=False)

    def _write_state(self) -> None:
        if not self._state:
            return
        path = Path(self._state["output_dir"]) / "session.json"
        path.write_text(json.dumps(self._state, indent=2, ensure_ascii=False), encoding="utf-8")

    def _device_info(self) -> dict[str, Any]:
        assert self._state is not None
        return {
            "profile": "direct",
            "device_id": self._state.get("device_serial"),
            "device_serial": self._state.get("device_serial"),
            "package_name": self._state.get("package_name"),
            "output_dir": self._state["output_dir"],
            "ingress": self._state.get("ingress", self._ingress),
            "auto_observe": self._state.get("auto_observe", True),
            "direct_status": self._state.get("status"),
            "artifacts": {
                "report_path": str(Path(self._state["output_dir"]) / "report.md"),
                "json_path": str(Path(self._state["output_dir"]) / "session.json"),
            },
        }

    def _persist(self, *, finished: bool = False) -> None:
        if not self._persist_history or not self._state:
            return
        storage = StorageManager(get_data_engine_db_path(), get_traces_dir())
        direct_status = str(self._state.get("status") or "running")
        status = (
            {"passed": "completed", "failed": "failed", "aborted": "cancelled",
             "incomplete": "cancelled"}.get(
                direct_status, "running"
            )
            if finished
            else "running"
        )
        metadata = SessionMetadata(
            session_id=self._state["session_id"],
            initial_goal=self._state["name"],
            start_time=float(self._state["started_at_epoch"]),
            end_time=time.time() if finished else None,
            status=status,
            device_info=self._device_info(),
            pid=None if finished else os.getpid(),
        )
        if finished:
            storage.update_session(metadata)
        else:
            storage.create_session(metadata)

    def _archive_observation(
        self, label: str, payload: dict[str, Any], image_bytes: bytes | None
    ) -> dict[str, Any]:
        assert self._state is not None
        archived = dict(payload)
        if image_bytes:
            index = (
                len(self._state["actions"])
                + len(self._state["checkpoints"])
                + len(self._state["observations"])
                + 1
            )
            path = (
                Path(self._state["output_dir"]) / "screenshots" / f"{index:03d}-{_slug(label)}.jpg"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image_bytes)
            archived["screenshot_path"] = str(path)
            archived["image_name"] = path.stem
        archived.pop("elements", None)
        return archived

    async def _capture(self, label: str) -> tuple[dict[str, Any] | None, bytes | None]:
        if not self._state:
            return None, None
        obs, image_bytes = await observe(
            self._context_factory(), self._controller_factory(), settle_ms=300
        )
        payload = self._archive_observation(label, obs.model_dump(mode="json"), image_bytes)
        return payload, image_bytes

    async def start(
        self, name: str, package_name: str | None = None, auto_observe: bool = True
    ) -> CallToolResult:
        if self._state:
            return self._result(
                "A Direct test session is already active.",
                {"ok": False, "session_id": self._state["session_id"]},
            )
        session_id = f"direct-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        output_dir = get_traces_dir() / session_id
        output_dir.mkdir(parents=True, exist_ok=False)
        self._state = {
            "schema_version": 2,
            "session_id": session_id,
            "name": name.strip() or "Direct mobile test",
            "package_name": package_name,
            "device_serial": self._device_serial,
            "ingress": self._ingress,
            "status": "running",
            "auto_observe": bool(auto_observe),
            "started_at": datetime.now().astimezone().isoformat(),
            "started_at_epoch": time.time(),
            "finished_at": None,
            "output_dir": str(output_dir),
            "actions": [],
            "observations": [],
            "checkpoints": [],
            "recordings": [],
            "summary": "",
            "artifacts": {},
        }
        self._write_state()
        await asyncio.to_thread(self._persist)
        return self._result(
            f"Direct test session started: {session_id}",
            {
                "ok": True,
                "session_id": session_id,
                "output_dir": str(output_dir),
                "auto_observe": bool(auto_observe),
            },
        )

    async def after_observe(
        self,
        observation: ObserveResult,
        image_bytes: bytes | None,
        wrapped: CallToolResult,
    ) -> CallToolResult:
        """Archive every explicit observation as durable session evidence."""
        if not self._state:
            return wrapped
        payload = self._archive_observation(
            "observe", observation.model_dump(mode="json"), image_bytes
        )
        item = {
            "index": len(self._state["observations"]) + 1,
            "timestamp": datetime.now().astimezone().isoformat(),
            "offset_ms": self._offset_ms(),
            "observation": payload,
        }
        self._state["observations"].append(item)
        self._write_state()
        if isinstance(wrapped.structuredContent, dict):
            wrapped.structuredContent.update(
                {
                    key: payload[key]
                    for key in ("screenshot_path", "image_name")
                    if key in payload
                }
            )
        return wrapped

    async def after_action(
        self,
        action: str,
        arguments: dict[str, Any],
        action_result: ActionResult,
        wrapped: CallToolResult,
    ) -> CallToolResult:
        if not self._state:
            return wrapped
        observation = None
        image_bytes = None
        completed_offset = self._offset_ms()
        if self._state["auto_observe"]:
            try:
                observation, image_bytes = await self._capture(action)
            except Exception as exc:  # Keep the action result even if observation fails.
                observation = {"ok": False, "message": str(exc)}
        event = {
            "index": len(self._state["actions"]) + 1,
            "timestamp": datetime.now().astimezone().isoformat(),
            "offset_ms": completed_offset,
            "action": action,
            "arguments": arguments,
            "result": action_result.model_dump(mode="json"),
            "observation": observation,
        }
        self._state["actions"].append(event)
        self._write_state()
        if observation:
            if isinstance(wrapped.structuredContent, dict):
                wrapped.structuredContent["post_action_observation"] = observation
            wrapped.content.append(
                TextContent(
                    type="text", text=f"Post-action observation: {observation.get('message')}"
                )
            )
        if image_bytes:
            wrapped.content.append(
                ImageContent(
                    type="image",
                    data=base64.b64encode(image_bytes).decode("utf-8"),
                    mimeType="image/jpeg",
                )
            )
        return wrapped

    async def checkpoint(
        self,
        name: str,
        passed: bool | None = None,
        details: str = "",
        capture_screen: bool = True,
        outcome: Literal["passed", "failed", "blocked", "skipped", "not_run"] | None = None,
        category: Literal["functional", "visual", "performance"] = "functional",
    ) -> CallToolResult:
        if not self._state:
            return self._result("No Direct test session is active.", {"ok": False})
        if outcome is None:
            if passed is None:
                return self._result("Supply passed or outcome.", {"ok": False})
            outcome = "passed" if passed else "failed"
        if outcome not in {"passed", "failed", "blocked", "skipped", "not_run"}:
            return self._result("Invalid checkpoint outcome.", {"ok": False})
        if category not in {"functional", "visual", "performance"}:
            return self._result("Invalid checkpoint category.", {"ok": False})
        if passed is not None and outcome != ("passed" if passed else "failed"):
            return self._result("passed and outcome disagree.", {"ok": False})
        observation = None
        image_bytes = None
        if capture_screen:
            try:
                observation, image_bytes = await self._capture(f"checkpoint-{name}")
            except Exception as exc:
                observation = {"ok": False, "message": str(exc)}
        item = {
            "index": len(self._state["checkpoints"]) + 1,
            "timestamp": datetime.now().astimezone().isoformat(),
            "offset_ms": self._offset_ms(),
            "name": name,
            "passed": outcome == "passed",
            "outcome": outcome,
            "category": category,
            "details": details,
            "observation": observation,
        }
        self._state["checkpoints"].append(item)
        self._write_state()
        return self._result(
            f"Checkpoint {outcome}: {name}",
            {"ok": True, "checkpoint": item},
            image_bytes,
        )

    def status(self) -> CallToolResult:
        if not self._state:
            return self._result(
                "No Direct test session is active.",
                {"ok": True, "active": False, "last_report": self._last_report},
            )
        return self._result(
            f"Direct test session {self._state['session_id']} is active.",
            {
                "ok": True,
                "active": True,
                "session_id": self._state["session_id"],
                "output_dir": self._state["output_dir"],
                "action_count": len(self._state["actions"]),
                "checkpoint_count": len(self._state["checkpoints"]),
                "auto_observe": self._state["auto_observe"],
                "recording": self._recording is not None,
            },
        )

    def _markdown(self) -> str:
        assert self._state is not None
        checkpoints = self._state["checkpoints"]
        passed = sum(1 for item in checkpoints if item["passed"])
        lines = [
            f"# {self._state['name']}",
            "",
            f"- Session: `{self._state['session_id']}`",
            f"- Status: **{self._state['status']}**",
            f"- Package: `{self._state['package_name'] or 'not specified'}`",
            f"- Started: {self._state['started_at']}",
            f"- Finished: {self._state['finished_at']}",
            f"- Actions: {len(self._state['actions'])}",
            f"- Explicit observations: {len(self._state['observations'])}",
            f"- Checkpoints: {passed}/{len(checkpoints)} passed",
            "",
            "## Summary",
            "",
            self._state["summary"] or "No summary supplied.",
            "",
            "## Checkpoints",
            "",
        ]
        if checkpoints:
            for item in checkpoints:
                mark = item["outcome"].upper()
                lines.append(
                    f"- **{mark}** [{item['category']}] — {item['name']}: {item['details'] or '-'}"
                )
        else:
            lines.append("- No checkpoints recorded.")
        lines.extend(["", "## Actions", ""])
        for item in self._state["actions"]:
            outcome = "OK" if item["result"]["ok"] else item["result"]["code"]
            lines.append(
                f"{item['index']}. `{item['action']}` — {outcome}: {item['result']['message']}"
            )
        if not self._state["actions"]:
            lines.append("No actions recorded.")
        evidence = []
        for item in self._state["observations"]:
            path = (item.get("observation") or {}).get("screenshot_path")
            if path:
                evidence.append((f"Observation {item['index']}", path))
        for item in checkpoints:
            path = (item.get("observation") or {}).get("screenshot_path")
            if path:
                evidence.append((f"Checkpoint: {item['name']}", path))
        for item in self._state["actions"]:
            path = (item.get("observation") or {}).get("screenshot_path")
            if path:
                evidence.append((f"Action {item['index']}", path))
        if evidence:
            lines.extend(["", "## Evidence Screenshots", ""])
            for label, path in evidence:
                lines.append(f"- {label}: `{path}`")
        logcat = self._state["artifacts"].get("logcat")
        if logcat:
            lines.extend(["", "## Diagnostics", "", f"- Logcat: `{logcat}`"])
            lines.append("- Coverage: bounded recent buffer; absence is not proof of no errors.")
        for key, value in self._state["artifacts"].items():
            if key.endswith("_error"):
                lines.append(f"- Evidence unavailable ({key}): {value}")
        if self._state.get("verdict_reasons"):
            lines.extend(["", "## Verdict limits", ""])
            lines.extend(f"- {reason}" for reason in self._state["verdict_reasons"])
        lines.extend(["", "## Timeline", "", "[Open offline evidence timeline](timeline.html)"])
        return "\n".join(lines) + "\n"

    async def finish(
        self,
        status: Literal["passed", "failed", "aborted", "incomplete"] = "passed",
        summary: str = "",
        include_logcat: bool = True,
        logcat_lines: int = 1000,
    ) -> CallToolResult:
        if not self._state:
            return self._result("No Direct test session is active.", {"ok": False})
        if status not in {"passed", "failed", "aborted", "incomplete"}:
            return self._result("Invalid session status.", {"ok": False})
        self._state["requested_status"] = status
        if self._recording is not None:
            await self.recording_stop()
        self._state["status"] = status
        self._state["summary"] = summary
        self._state["finished_at"] = datetime.now().astimezone().isoformat()
        final_image = None
        try:
            final_obs, final_image = await self._capture("final")
            self._state["artifacts"]["final_observation"] = final_obs
            if final_obs and final_obs.get("ok") is False:
                self._state["artifacts"]["final_observation_error"] = final_obs.get(
                    "message", "Final screenshot failed"
                )
        except Exception as exc:
            self._state["artifacts"]["final_observation_error"] = str(exc)
        if include_logcat:
            try:
                logs = await asyncio.to_thread(
                    fetch_and_filter_logs,
                    self._context_factory(),
                    max(1, min(int(logcat_lines), 5000)),
                    None,
                    None,
                )
                log_path = Path(self._state["output_dir"]) / "logcat.txt"
                log_path.write_text(logs, encoding="utf-8")
                self._state["artifacts"]["logcat"] = str(log_path)
            except Exception as exc:
                self._state["artifacts"]["logcat_error"] = str(exc)
        coverage: dict[str, dict[str, int]] = {}
        checks = self._state["checkpoints"]
        for check in checks:
            counts = coverage.setdefault(check["category"], {})
            counts[check["outcome"]] = counts.get(check["outcome"], 0) + 1
        self._state["coverage"] = coverage
        reasons = []
        if any(check["outcome"] == "failed" for check in checks):
            reasons.append("One or more checkpoints failed.")
            if status != "aborted":
                self._state["status"] = "failed"
        if not checks or any(check["outcome"] not in {"passed", "failed"} for check in checks):
            reasons.append("Some scenarios are unverified, or no checkpoints were recorded.")
        if any(key.endswith("_error") for key in self._state["artifacts"]):
            reasons.append("Requested diagnostic evidence could not be collected.")
        for collection in ("actions", "observations", "checkpoints"):
            for event in self._state[collection]:
                evidence = event.get("observation") or {}
                if evidence.get("ok") is False:
                    reasons.append(
                        f"{collection} #{event['index']} evidence unavailable: "
                        f"{evidence.get('message', 'capture failed')}"
                    )
        if reasons and self._state["status"] == "passed":
            self._state["status"] = "incomplete"
        self._state["verdict_reasons"] = reasons
        output_dir = Path(self._state["output_dir"])
        report_path = output_dir / "report.md"
        json_path = output_dir / "session.json"
        try:
            timeline_path = write_timeline(self._state)
            self._state["artifacts"]["timeline"] = str(timeline_path)
        except Exception as exc:
            self._state["artifacts"]["timeline_error"] = str(exc)
            if self._state["status"] == "passed":
                self._state["status"] = "incomplete"
        self._write_state()
        report_path.write_text(self._markdown(), encoding="utf-8")
        await asyncio.to_thread(self._persist, finished=True)
        session_id = self._state["session_id"]
        result_status = self._state["status"]
        timeline_result = self._state["artifacts"].get("timeline")
        self._last_report = str(report_path)
        self._state = None
        return self._result(
            f"Direct test session {session_id} finished with status {result_status}.",
            {
                "ok": True,
                "session_id": session_id,
                "status": result_status,
                "report_path": str(report_path),
                "json_path": str(json_path),
                "timeline_path": timeline_result,
            },
            final_image,
        )
