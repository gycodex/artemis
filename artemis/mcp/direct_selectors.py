"""Fresh-hierarchy selectors and bounded waits for host-driven testing."""

import asyncio
import math
import re
import time
from typing import Literal
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field, model_validator

from artemis.drivers.factory import get_driver
from artemis.mcp.action_types import ActionCode, ActionResult


class Selector(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = Field(default=None, min_length=1)
    resource_id: str | None = Field(default=None, min_length=1)
    content_desc: str | None = Field(default=None, min_length=1)
    package: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None

    @model_validator(mode="after")
    def has_identity(self):
        if not any((self.text, self.resource_id, self.content_desc, self.package)):
            raise ValueError("A selector needs text, resource_id, content_desc or package.")
        return self

    def matches(self, node: dict) -> bool:
        aliases = {"resource_id": "resource-id", "content_desc": "content-desc"}
        for key, expected in self.model_dump(exclude_none=True).items():
            actual = node.get(key, node.get(aliases.get(key, key)))
            if key == "enabled" and isinstance(actual, str):
                actual = actual == "true"
            if actual != expected:
                return False
        return True


class DirectSelectors:
    def __init__(self, actuator):
        self.actuator = actuator

    async def _screen(self):
        screen = await get_driver(self.actuator.ctx).get_screen_data()
        if not screen.ui_hierarchy_xml and not screen.ui_elements:
            raise ValueError("UI hierarchy unavailable; absence cannot be established")
        if screen.ui_hierarchy_xml is not None:
            ET.fromstring(screen.ui_hierarchy_xml)
        if screen.width <= 0 or screen.height <= 0:
            raise ValueError("Invalid display dimensions")
        return screen

    async def click(self, selector: Selector, fallback_target: list[int] | None = None):
        action = "click_element"
        if fallback_target is not None and (
            len(fallback_target) != 2 or
            any(type(v) is not int or not 0 <= v <= 1000 for v in fallback_target)
        ):
            return ActionResult.failure(action, "Fallback must be two integers in 0..1000.",
                                        code=ActionCode.INVALID_ARGS)
        try:
            screen = await asyncio.wait_for(self._screen(), timeout=10)
            nodes = [n for n in screen.ui_elements if selector.matches(n)]
            if len(nodes) > 1:
                return ActionResult.failure(action, "Selector is ambiguous; narrow the selector.",
                                            code=ActionCode.INVALID_ARGS)
            if not nodes:
                if fallback_target is None:
                    return ActionResult.failure(action, "No matching element.",
                                                code=ActionCode.TARGET_NOT_FOUND)
                target = fallback_target
                message = "Used explicit coordinate fallback."
            else:
                node = nodes[0]
                if node.get("enabled") in (False, "false"):
                    return ActionResult.failure(action, "Matched element is disabled.",
                                                code=ActionCode.TARGET_NOT_FOUND)
                bounds = node.get("bounds")
                if isinstance(bounds, str):
                    match = re.fullmatch(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", bounds)
                    bounds = list(map(int, match.groups())) if match else None
                if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
                    raise ValueError("Matched element has no valid bounds")
                x1, y1, x2, y2 = map(float, bounds)
                if not all(math.isfinite(v) for v in (x1, y1, x2, y2)):
                    raise ValueError("Non-finite element bounds")
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(screen.width, x2), min(screen.height, y2)
                if x1 >= x2 or y1 >= y2:
                    raise ValueError("Matched element is outside the display")
                target = [round((x1 + x2) * 500 / screen.width),
                          round((y1 + y2) * 500 / screen.height)]
                message = "Clicked unique matching element."
            self.actuator.ctx.device.device_width = screen.width
            self.actuator.ctx.device.device_height = screen.height
            result = await self.actuator.click(*target)
            return result.model_copy(update={"action": action, "message": message}) if result.ok else result
        except Exception as exc:
            return ActionResult.failure(action, f"Cannot locate element: {exc}")

    async def wait(
        self, selector: Selector,
        state: Literal["present", "absent", "enabled", "stable"] = "present",
        timeout_ms: int = 5000, stable_ms: int = 500,
    ) -> ActionResult:
        if state not in {"present", "absent", "enabled", "stable"} or not (
            1 <= timeout_ms <= 60000 and 1 <= stable_ms <= 60000
        ):
            return ActionResult.failure("wait_for_element", "Invalid wait bounds or state.",
                                        code=ActionCode.INVALID_ARGS)
        started = time.monotonic()
        deadline = started + timeout_ms / 1000
        previous = None
        stable_since = None
        try:
            while time.monotonic() < deadline:
                screen = await asyncio.wait_for(self._screen(), deadline - time.monotonic())
                nodes = [n for n in screen.ui_elements if selector.matches(n)]
                ready = (state == "absent" and not nodes) or (state == "present" and bool(nodes))
                if state == "enabled":
                    ready = len(nodes) == 1 and nodes[0].get("enabled") in (True, "true")
                if state == "stable":
                    signature = nodes if len(nodes) == 1 else None
                    if signature is None or signature != previous:
                        stable_since = time.monotonic() if signature is not None else None
                    previous = signature
                    ready = stable_since is not None and (
                        time.monotonic() - stable_since >= stable_ms / 1000
                    )
                if ready:
                    return ActionResult.success("wait_for_element", f"Element state: {state}.",
                                                duration_ms=int((time.monotonic() - started) * 1000))
                await asyncio.sleep(min(0.2, max(0, deadline - time.monotonic())))
        except TimeoutError:
            pass
        except Exception as exc:
            return ActionResult.failure("wait_for_element", f"Hierarchy unavailable: {exc}")
        return ActionResult.failure("wait_for_element", f"Timed out waiting for {state}.",
                                    code=ActionCode.TIMEOUT)

    async def wait_app(self, package_name: str, timeout_ms: int = 5000) -> ActionResult:
        if not 1 <= timeout_ms <= 60000:
            return ActionResult.failure("wait_for_app", "timeout_ms must be in 1..60000.",
                                        code=ActionCode.INVALID_ARGS)
        deadline = time.monotonic() + timeout_ms / 1000
        try:
            driver = get_driver(self.actuator.ctx)
            while time.monotonic() < deadline:
                current = await asyncio.wait_for(driver.get_current_package(),
                                                 deadline - time.monotonic())
                if current == package_name:
                    return ActionResult.success("wait_for_app", f"Foreground: {package_name}.")
                await asyncio.sleep(min(0.2, max(0, deadline - time.monotonic())))
        except TimeoutError:
            pass
        except Exception as exc:
            return ActionResult.failure("wait_for_app", f"Foreground unavailable: {exc}")
        return ActionResult.failure("wait_for_app", f"Timed out waiting for {package_name}.",
                                    code=ActionCode.TIMEOUT)
