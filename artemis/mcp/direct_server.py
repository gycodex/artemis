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

"""Host-driven MCP server for Codex, Claude Code, and other MCP clients.

Unlike the autonomous ``mcp_server`` entrypoint, this server does not create an
Artemis LLM.  The connected MCP host owns the observe/decide/act loop while
Artemis supplies typed device actions, screenshots, UI hierarchy, and Logcat.
"""

import asyncio
from contextlib import asynccontextmanager
import re
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from artemis.mcp.action_manifest import DEVICE_ACTIONS
from artemis.mcp.action_server import build_action_server
from artemis.mcp.actuators.adb import AdbActuator
from artemis.mcp.actuators.base import Actuator
from artemis.mcp.direct_session import DirectSession
from artemis.tools.mobile.log_utils import fetch_and_filter_logs
from third_party.mobile_use.controllers.platform_specific_commands_controller import (
    get_adb_device,
)

__all__ = ["LazyAdbActuator", "build_direct_adb_server", "build_direct_server"]


class LazyAdbActuator:
    """Delay device discovery until the host calls its first device tool.

    Codex may start one MCP process for every conversation it restores. Doing
    UIAutomator setup before the MCP initialize handshake makes those processes
    contend for the same emulator and can prevent conversation hydration.
    """

    def __init__(self, device_serial: str | None = None):
        self.device_serial = device_serial
        self._actuator: AdbActuator | None = None

    def capabilities(self) -> frozenset[str]:
        return DEVICE_ACTIONS

    def extensions(self) -> list:
        return []

    def _resolve(self) -> AdbActuator:
        if self._actuator is None:
            from artemis.mcp.adb_server import _get_controller

            controller = _get_controller(self.device_serial)
            self._actuator = AdbActuator(controller.ctx, controller)
        return self._actuator

    @property
    def ctx(self):
        return self._resolve().ctx

    @property
    def controller(self):
        return self._resolve().controller

    def __getattr__(self, name: str):
        return getattr(self._resolve(), name)


def _result(message: str, payload: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        structuredContent=payload,
        isError=False,
    )


def _filter_logcat(
    logs: str,
    package_name: str | None,
    errors_only: bool,
    process_ids: set[str] | None = None,
) -> str:
    lines = logs.splitlines()
    if package_name:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+", package_name):
            raise ValueError("Invalid Android package name")
        package_lower = package_name.lower()
        process_ids = set(process_ids or ())
        package_pattern = re.escape(package_name) + r"(?::[A-Za-z0-9_.]+)?"
        # Include processes that have exited before pidof, including SDK services.
        for line in lines:
            started = re.search(rf"\bStart proc (\d+):{package_pattern}/", line)
            crashed = re.search(rf"\bProcess: {package_pattern}, PID: (\d+)", line)
            if match := started or crashed:
                process_ids.add(match.group(1))

        def belongs_to_package(line: str) -> bool:
            if re.search(rf"(?<![\w.]){re.escape(package_lower)}(?=[:/\s,}}]|$)", line.lower()):
                return True
            # Android's threadtime format starts with date, time, PID, and TID.
            match = re.match(r"^\S+\s+\S+\s+(\d+)\s+\d+\s+", line)
            return bool(match and match.group(1) in process_ids)

        lines = [line for line in lines if belongs_to_package(line)]
    if errors_only:
        error_pattern = re.compile(
            r"(?:^\S+\s+\S+\s+\d+\s+\d+\s+[EF]\s|(?:^|\s)[EF]/"
            r"|fatal exception|fatal signal|\banr\b|am_crash|crash)",
            re.IGNORECASE,
        )
        lines = [line for line in lines if error_pattern.search(line)]
    return "\n".join(lines)


def build_direct_server(
    actuator: Actuator,
    name: str = "artemis_device",
    *,
    ingress: str = "mcp-direct",
    persist_history: bool = True,
) -> FastMCP:
    """Build a no-LLM device server around an initialized actuator."""

    def current_ctx():
        return getattr(actuator, "ctx", None)

    def current_controller():
        return getattr(actuator, "controller", None)

    session = DirectSession(
        current_ctx,
        current_controller,
        device_serial=getattr(actuator, "device_serial", None),
        ingress=ingress,
        persist_history=persist_history,
    )

    @asynccontextmanager
    async def lifespan(_server):
        try:
            yield
        finally:
            await session.close()

    server = build_action_server(
        actuator,
        name=name,
        after_action=session.after_action,
        after_observe=session.after_observe,
        lifespan=lifespan,
    )

    @server.tool()
    async def recording_start() -> CallToolResult:
        """Record continuous video into the active Direct session (scrcpy/ffmpeg required)."""
        return await session.recording_start()

    @server.tool()
    async def recording_stop() -> CallToolResult:
        """Finalize this session's owned video and segmented timeline."""
        return await session.recording_stop()

    @server.tool()
    async def session_start(
        name: str,
        package_name: str | None = None,
        auto_observe: bool = True,
    ) -> CallToolResult:
        """Start a host-driven test session that records actions and screenshots."""
        return await session.start(name, package_name, auto_observe)

    @server.tool()
    async def session_checkpoint(
        name: str,
        passed: bool | None = None,
        details: str = "",
        capture_screen: bool = True,
        outcome: Literal["passed", "failed", "blocked", "skipped", "not_run"] | None = None,
        category: Literal["functional", "visual", "performance"] = "functional",
    ) -> CallToolResult:
        """Record a host-evaluated assertion, optionally with the current screen."""
        return await session.checkpoint(name, passed, details, capture_screen, outcome, category)

    @server.tool()
    async def session_status() -> CallToolResult:
        """Return the active Direct session and artifact counters, if any."""
        return session.status()

    @server.tool()
    async def session_finish(
        status: Literal["passed", "failed", "aborted", "incomplete"] = "passed",
        summary: str = "",
        include_logcat: bool = True,
        logcat_lines: int = 1000,
    ) -> CallToolResult:
        """Finish the session and write Markdown, JSON, screenshots, and Logcat."""
        return await session.finish(status, summary, include_logcat, logcat_lines)

    @server.tool()
    async def list_devices() -> CallToolResult:
        """List Android device serials visible to this ADB server and show the selected one."""
        ctx = current_ctx()
        selected = getattr(getattr(ctx, "device", None), "device_id", None)
        try:
            adb_client = ctx.get_adb_client() if ctx is not None else None
            devices = await asyncio.to_thread(adb_client.device_list) if adb_client else []
            entries = [
                {"serial": device.serial, "selected": device.serial == selected}
                for device in devices
            ]
            serials = ", ".join(entry["serial"] for entry in entries) or "none"
            return _result(
                f"Connected Android devices: {serials}. Selected: {selected or 'none'}.",
                {"ok": True, "selected_serial": selected, "devices": entries},
            )
        except Exception as exc:
            return _result(
                f"Failed to list Android devices: {exc}",
                {"ok": False, "selected_serial": selected, "devices": [], "error": str(exc)},
            )

    @server.tool()
    async def get_device_info() -> CallToolResult:
        """Return the selected Android device identity, display size, and OS version."""
        ctx = current_ctx()
        device_ctx = getattr(ctx, "device", None)
        payload: dict[str, Any] = {
            "ok": device_ctx is not None,
            "serial": getattr(device_ctx, "device_id", None),
            "width": getattr(device_ctx, "device_width", None),
            "height": getattr(device_ctx, "device_height", None),
        }
        try:
            device = get_adb_device(ctx)
            if device is not None:
                model, android_version = await asyncio.gather(
                    asyncio.to_thread(device.shell, "getprop ro.product.model"),
                    asyncio.to_thread(device.shell, "getprop ro.build.version.release"),
                )
                payload.update(
                    model=str(model).strip(),
                    android_version=str(android_version).strip(),
                )
        except Exception as exc:  # Device metadata is useful but not required for control.
            payload["warning"] = str(exc)
        return _result("Selected Android device.", payload)

    @server.tool()
    async def read_logcat(
        lines: int = 500,
        package_name: str | None = None,
        errors_only: bool = False,
        since_time: str | None = None,
    ) -> CallToolResult:
        """Read a bounded Logcat snapshot without clearing or changing device logs.

        ``package_name`` performs a text filter suitable for quick app diagnostics.
        ``errors_only`` keeps common error, crash, fatal-exception, and ANR records.
        ``since_time`` accepts Android's ``MM-DD HH:MM:SS.mmm`` timestamp format.
        """
        safe_lines = max(1, min(int(lines), 5000))
        try:
            ctx = current_ctx()
            process_ids: set[str] = set()
            if package_name:
                if not re.fullmatch(
                    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+", package_name
                ):
                    raise ValueError("Invalid Android package name")
                device = get_adb_device(ctx)
                if device is not None:
                    pid_output = await asyncio.to_thread(device.shell, f"pidof {package_name}")
                    process_ids = set(str(pid_output).split())
                    processes = await asyncio.to_thread(device.shell, "ps -A")
                    for line in str(processes).splitlines():
                        fields = line.split()
                        if len(fields) > 2 and (
                            fields[-1] == package_name or fields[-1].startswith(package_name + ":")
                        ) and fields[1].isdigit():
                            process_ids.add(fields[1])
            logs = await asyncio.to_thread(
                fetch_and_filter_logs,
                ctx,
                safe_lines,
                since_time,
                None,
            )
            filtered = _filter_logcat(logs, package_name, errors_only, process_ids)
            payload = {
                "ok": True,
                "requested_lines": safe_lines,
                "returned_lines": len(filtered.splitlines()) if filtered else 0,
                "package_name": package_name,
                "errors_only": errors_only,
                "since_time": since_time,
                "source_lines": len(logs.splitlines()),
                "possibly_truncated": len(logs.splitlines()) >= safe_lines,
                "coverage": "bounded_snapshot_not_proof_of_no_errors",
            }
            return _result(filtered or "No matching Logcat entries.", payload)
        except Exception as exc:
            return _result(
                f"Failed to read Logcat: {exc}",
                {"ok": False, "error": str(exc)},
            )

    return server


def build_direct_adb_server(
    device_serial: str | None = None,
    name: str = "artemis_device",
    *,
    ingress: str = "mcp-direct",
    persist_history: bool = True,
) -> FastMCP:
    """Build a Direct server whose ADB connection is established on first tool use."""
    return build_direct_server(
        LazyAdbActuator(device_serial),
        name=name,
        ingress=ingress,
        persist_history=persist_history,
    )
