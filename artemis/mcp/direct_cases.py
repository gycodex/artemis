"""Declarative Direct regression cases; no arbitrary Python or shell execution."""

import argparse
import asyncio
import json
from pathlib import Path
import re
import time
from typing import Any, Literal
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field


class CaseStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    assertion: bool = False
    category: Literal["functional", "visual", "performance"] = "functional"


class DirectCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    package_name: str | None = None
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    steps: list[CaseStep] = Field(min_length=1, max_length=200)
    record_video: bool = False
    include_logcat: bool = False
    timeout_ms: int = Field(default=300000, ge=1, le=600000)


ASSERTIONS = {"wait_for_element", "wait_for_app"}
ALLOWED = ASSERTIONS | {
    "click", "click_sequence", "click_element", "long_press", "input_text", "swipe",
    "press_key", "manage_app", "wait_for_delay", "open_link", "erase_one_char",
    "focus_and_clear_text",
}


def validate_action_arguments(tool: str, args: dict) -> None:
    """The historical wire schema types coordinates as lists without shape limits."""
    def point(value):
        if not isinstance(value, list) or len(value) != 2 or any(
            type(v) is not int or not 0 <= v <= 1000 for v in value
        ):
            raise ValueError(f"{tool}: coordinates must be two integers in 0..1000")

    for key in ("target", "start", "end", "fallback_target"):
        if args.get(key) is not None:
            point(args[key])
    if tool == "click_sequence":
        if not 1 <= len(args["sequence"]) <= 200:
            raise ValueError("click_sequence requires 1..200 points")
        for value in args["sequence"]:
            point(value)
    for key in ("timeout_ms", "stable_ms", "duration_ms", "time_in_ms", "delay_ms"):
        if args.get(key) is not None and not (
            (0 if key in {"delay_ms", "time_in_ms"} else 1) <= args[key] <= 60000
        ):
            raise ValueError(f"{key} is outside the supported millisecond range")
    if "times" in args and not 1 <= args["times"] <= 100:
        raise ValueError("times must be in 1..100")
    if tool == "manage_app" and (
        args["action"] not in {"launch", "stop"} or not args["app_name"].strip()
    ):
        raise ValueError("manage_app needs launch/stop and an app name")
    if tool == "wait_for_app" and not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", args["package_name"]):
        raise ValueError("wait_for_app needs an Android package name")
    if tool == "open_link" and not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", args["url"]):
        raise ValueError("open_link needs an absolute URL")


def substitute(value: Any, parameters: dict) -> Any:
    if isinstance(value, dict):
        return {key: substitute(item, parameters) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, parameters) for item in value]
    if isinstance(value, str):
        if match := re.fullmatch(r"\$\{([^}]+)\}", value):
            return parameters[match.group(1)]
        return re.sub(r"\$\{([^}]+)\}", lambda m: str(parameters[m.group(1)]), value)
    return value


def _write_junit(path: Path, name: str, results: list[dict], status: str) -> None:
    evidence_error = status != "passed" and not any(r["status"] == "failed" for r in results)
    suite = ET.Element("testsuite", name=name, tests=str(len(results) + int(evidence_error)),
                       failures=str(sum(r["status"] == "failed" for r in results)),
                       errors=str(int(evidence_error)),
                       skipped=str(sum(r["status"] == "not_run" for r in results)))
    for result in results:
        item = ET.SubElement(suite, "testcase", name=result["name"],
                             time=f'{result.get("duration_ms", 0) / 1000:.3f}')
        if result["status"] == "failed":
            ET.SubElement(item, "failure", message=result["message"]).text = result["message"]
        elif result["status"] == "not_run":
            ET.SubElement(item, "skipped", message="Earlier step failed")
    if evidence_error:
        item = ET.SubElement(suite, "testcase", name="Session evidence")
        ET.SubElement(item, "error", message=f"Session is {status}; see report.md")
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


async def run_case(server, session, case: dict) -> Any:
    """Validate the complete case before starting a session or touching the device."""
    if session.active:
        return session._result("Finish the active session before running a case.", {"ok": False})
    try:
        spec = DirectCase.model_validate(case)
        prepared = []
        if not any(step.assertion for step in spec.steps):
            raise ValueError("A regression case must include at least one wait assertion.")
        for step in spec.steps:
            if step.tool not in ALLOWED or (step.assertion and step.tool not in ASSERTIONS):
                raise ValueError(f"Unsupported action/assertion tool: {step.tool}")
            tool = server._tool_manager.get_tool(step.tool)
            if tool is None:
                raise ValueError(f"Device does not support {step.tool}")
            args = substitute(step.arguments, spec.parameters)
            model = tool.fn_metadata.arg_model
            if set(args) - set(model.model_fields):
                raise ValueError(f"Unknown arguments for {step.tool}")
            validated = model.model_validate(args)
            validated_args = validated.model_dump()
            validate_action_arguments(step.tool, validated_args)
            prepared.append((step, tool, validated_args))
    except (ValueError, KeyError) as exc:
        return session._result(f"Invalid case: {exc}", {"ok": False})

    await session.start(spec.name, spec.package_name)
    results = []
    failed = False
    deadline = time.monotonic() + spec.timeout_ms / 1000
    try:
        if spec.record_video:
            recording = await session.recording_start()
            if not recording.structuredContent["ok"]:
                failed = True
        for step, tool, args in prepared:
            if failed:
                results.append({"name": step.name, "status": "not_run", "message": "Earlier failure"})
                if step.assertion:
                    await session.checkpoint(step.name, outcome="not_run",
                                             category=step.category, capture_screen=False)
                continue
            start = time.monotonic()
            try:
                remaining = deadline - start
                if remaining <= 0:
                    raise TimeoutError("Case deadline exceeded")
                raw = await asyncio.wait_for(tool.run(args, convert_result=False), remaining)
                payload = raw.structuredContent or {}
                ok = payload.get("ok") is True
                message = "\n".join(b.text for b in raw.content if b.type == "text")
            except Exception as exc:
                ok, message = False, str(exc) or "Case deadline exceeded"
            results.append({"name": step.name, "status": "passed" if ok else "failed",
                            "message": message, "duration_ms": int((time.monotonic() - start) * 1000)})
            if step.assertion or not ok:
                await session.checkpoint(step.name, passed=ok, details=message,
                                         category=step.category, capture_screen=not ok)
            failed = not ok
        finished = await session.finish("failed" if failed else "passed",
                                        f"Regression case: {spec.name}",
                                        include_logcat=spec.include_logcat)
        output = Path(finished.structuredContent["json_path"]).parent
        junit_path = output / "junit.xml"
        _write_junit(junit_path, spec.name, results, finished.structuredContent["status"])
        (output / "case-results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False),
                                                 encoding="utf-8")
        finished.structuredContent.update(junit_path=str(junit_path), steps=results)
        return finished
    finally:
        if session.active:
            await session.close()


def main() -> int:
    """Run one JSON case or a {cases: [...]} suite; rerun failed cases with prerequisites."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--device", required=True)
    parser.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--failed-from", type=Path, help="Previous suite-results.json")
    parser.add_argument("--output", type=Path, default=Path("suite-results.json"))
    args = parser.parse_args()
    document = json.loads(args.path.read_text(encoding="utf-8"))
    cases = document.get("cases", [document])
    if args.failed_from:
        previous = json.loads(args.failed_from.read_text(encoding="utf-8"))
        names = {row["name"] for row in previous if row["status"] != "passed"}
        cases = [case for case in cases if case["name"] in names]
    for value in args.param:
        if "=" not in value:
            parser.error("--param requires NAME=VALUE")
        key, val = value.split("=", 1)
        for case in cases:
            case.setdefault("parameters", {})[key] = val

    async def execute():
        from artemis.mcp.action_session import ActionSession
        from artemis.mcp.direct_server import build_direct_adb_server
        client = await ActionSession(build_direct_adb_server(args.device), close_timeout=60.0).start()
        results = []
        try:
            for case in cases:
                result = await client.call_raw("run_case", {"case": case})
                payload = result.structuredContent or {}
                results.append({"name": case["name"], "status": payload.get("status", "failed"),
                                "report_path": payload.get("report_path"),
                                "junit_path": payload.get("junit_path"),
                                "message": result.content[0].text if result.content else ""})
        finally:
            await client.aclose()
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0 if all(row["status"] == "passed" for row in results) else 1

    return asyncio.run(execute())


if __name__ == "__main__":
    raise SystemExit(main())
