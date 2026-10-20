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

"""Model-free Direct device control for the Web console."""

import asyncio
from typing import Any

from artemis.mcp.action_session import ActionSession
from artemis.mcp.direct_server import build_direct_adb_server
from artemis.runtime import DeviceExecutionLock

try:
    from admin_console.database.repositories.session_repository import session_repo
except ImportError:
    from apps.admin_console.database.repositories.session_repository import session_repo

DIRECT_WEB_ACTIONS = frozenset(
    {
        "click",
        "long_press",
        "input_text",
        "swipe",
        "press_key",
        "manage_app",
        "wait_for_delay",
        "open_link",
        "erase_one_char",
        "focus_and_clear_text",
        "recording_start",
        "recording_stop",
        "click_element",
        "wait_for_element",
        "wait_for_app",
    }
)


def _serialize_result(result: Any) -> dict[str, Any]:
    image_data_url = None
    texts: list[str] = []
    for block in getattr(result, "content", []) or []:
        if getattr(block, "type", None) == "text":
            texts.append(block.text)
        elif getattr(block, "type", None) == "image" and image_data_url is None:
            image_data_url = f"data:{block.mimeType};base64,{block.data}"
    return {
        "result": result.structuredContent or {},
        "texts": texts,
        "image_data_url": image_data_url,
    }


class DirectControlService:
    """Own one browser-driven Direct session and its device lease."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._action_session: ActionSession | None = None
        self._device_lock: DeviceExecutionLock | None = None
        self._device_serial: str | None = None
        self._session_id: str | None = None

    @property
    def active(self) -> bool:
        return self._action_session is not None and self._action_session.started

    async def start(
        self,
        device_serial: str,
        name: str,
        package_name: str | None,
        auto_observe: bool,
    ) -> dict[str, Any]:
        async with self._guard:
            if self.active:
                status = await self._action_session.call_raw("session_status", {})
                payload = _serialize_result(status)
                payload["device_serial"] = self._device_serial
                return payload

            device_lock = DeviceExecutionLock(
                device_serial,
                f"Web Direct session: {name}",
                ingress="web-direct",
            )
            await asyncio.to_thread(device_lock.acquire, blocking=False)
            action_session = ActionSession(
                build_direct_adb_server(device_serial, ingress="web-direct"),
                close_timeout=60.0,
            )
            try:
                await action_session.start()
                result = await action_session.call_raw(
                    "session_start",
                    {
                        "name": name,
                        "package_name": package_name,
                        "auto_observe": auto_observe,
                    },
                )
            except BaseException:
                await action_session.aclose()
                device_lock.release()
                raise

            if not (result.structuredContent or {}).get("ok"):
                await action_session.aclose()
                device_lock.release()
                return _serialize_result(result)

            self._action_session = action_session
            self._device_lock = device_lock
            self._device_serial = device_serial
            payload = _serialize_result(result)
            payload["device_serial"] = device_serial
            result_data = payload["result"]
            self._session_id = str(result_data["session_id"])
            try:
                await asyncio.to_thread(
                    session_repo.create_direct_session,
                    self._session_id,
                    name,
                    device_serial,
                    package_name,
                    str(result_data["output_dir"]),
                    auto_observe,
                )
            except BaseException:
                await self._close_unlocked()
                raise
            return payload

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with self._guard:
            if not self.active:
                raise RuntimeError("No Web Direct session is active.")
            result = await self._action_session.call_raw(tool, arguments)
            payload = _serialize_result(result)
            payload["device_serial"] = self._device_serial
            return payload

    async def observe(self, settle_ms: int = 300) -> dict[str, Any]:
        return await self.call(
            "observe_screen",
            {"settle_ms": max(0, min(settle_ms, 5000)), "include_image": True},
        )

    async def finish(self, arguments: dict[str, Any]) -> dict[str, Any]:
        async with self._guard:
            if not self.active:
                raise RuntimeError("No Web Direct session is active.")
            try:
                result = await self._action_session.call_raw("session_finish", arguments)
                payload = _serialize_result(result)
                payload["device_serial"] = self._device_serial
                result_data = payload["result"]
                if result_data.get("ok") and self._session_id:
                    await asyncio.to_thread(
                        session_repo.finish_direct_session,
                        self._session_id,
                        str(result_data.get("status") or arguments.get("status") or "failed"),
                        result_data.get("report_path"),
                        result_data.get("json_path"),
                    )
                return payload
            finally:
                await self._close_unlocked()

    async def status(self) -> dict[str, Any]:
        async with self._guard:
            if not self.active:
                return {
                    "result": {"ok": True, "active": False},
                    "texts": [],
                    "image_data_url": None,
                    "device_serial": None,
                }
            result = await self._action_session.call_raw("session_status", {})
            payload = _serialize_result(result)
            payload["device_serial"] = self._device_serial
            return payload

    async def close(self) -> None:
        async with self._guard:
            await self._close_unlocked()

    async def _close_unlocked(self) -> None:
        unfinished_session_id = self._session_id
        try:
            if self._action_session is not None:
                await self._action_session.aclose()
        finally:
            if self._device_lock is not None:
                self._device_lock.release()
            self._action_session = None
            self._device_lock = None
            self._device_serial = None
            self._session_id = None
            unfinished_status = (
                await asyncio.to_thread(session_repo.get_session_status, unfinished_session_id)
                if unfinished_session_id
                else None
            )
            if unfinished_session_id and unfinished_status == "running":
                await asyncio.to_thread(
                    session_repo.finish_direct_session,
                    unfinished_session_id,
                    "aborted",
                )


direct_control_service = DirectControlService()
