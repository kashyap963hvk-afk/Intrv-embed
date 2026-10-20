"""Code execution through Piston (https://github.com/engineer-man/piston, MIT), a separate container with no
network and per-run CPU, memory and time limits. The backend never compiles or runs candidate code itself.

Static analysis rides on the same compile: GCC warning groups are switched on with `#pragma GCC diagnostic`
lines and `#line 1` keeps the candidate's line numbers, so no compiler flags are needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.rolepacks.schema import PracticalTask

LANGUAGES = {
    "c": ("c", "main.c"),
    "cpp": ("c++", "main.cpp"),
    "python": ("python", "main.py"),
    "rust": ("rust", "main.rs"),
}
WARNINGS = ("-Wall", "-Wextra", "-Wshadow", "-Wconversion", "-Wcast-align", "-Wdouble-promotion")
OUTPUT_LIMIT = 8_000
RUN_TIMEOUT_MS = 3_000
COMPILE_TIMEOUT_MS = 10_000
MEMORY_LIMIT = 128 * 1024 * 1024
_LINK_ERROR = re.compile(r"undefined reference|ld returned|collect2|multiple definition", re.IGNORECASE)


class PistonUnavailableError(Exception):
    pass


@dataclass(slots=True)
class Execution:
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    signal: str | None = None
    compile_output: str = ""
    compile_failed: bool = False


@dataclass(slots=True)
class TestOutcome:
    stdin: str
    expected: str
    actual: str
    passed: bool
    detail: str = ""


@dataclass(slots=True)
class RunReport:
    compiled: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    tests: list[TestOutcome] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for t in self.tests if t.passed)

    def as_json(self) -> dict[str, Any]:
        return {
            "compiled": self.compiled,
            "warnings": self.warnings,
            "errors": self.errors,
            "tests": [
                {"stdin": t.stdin, "expected": t.expected, "actual": t.actual, "passed": t.passed, "detail": t.detail}
                for t in self.tests
            ],
            "passed": self.passed,
            "total": len(self.tests),
        }


def same_output(actual: str, expected: str) -> bool:
    """Judge-style comparison: line by line, ignoring trailing whitespace and trailing blank lines."""

    def lines(text: str) -> list[str]:
        return [line.rstrip() for line in text.rstrip().splitlines()]

    return lines(actual) == lines(expected)


def with_warnings(code: str, language: str) -> str:
    if language not in {"c", "cpp"}:
        return code
    pragmas = "".join(f'#pragma GCC diagnostic warning "{w}"\n' for w in WARNINGS)
    return f"{pragmas}#line 1\n{code}"


def diagnostics(output: str) -> tuple[list[str], list[str]]:
    """(warnings, errors) from compiler output; link errors (missing hardware functions in snippets) are dropped."""
    warnings, errors = [], []
    for line in output.splitlines():
        if _LINK_ERROR.search(line):
            continue
        if ": warning:" in line:
            warnings.append(line.split("main.", 1)[-1][:300])
        elif ": error:" in line:
            errors.append(line.split("main.", 1)[-1][:300])
    return warnings[:40], errors[:40]


class PistonClient:
    def __init__(self, base_url: str, http: httpx.AsyncClient, *, version: str = "*") -> None:
        self._url = base_url.rstrip("/") + "/api/v2/execute"
        self._http = http
        self._version = version

    async def execute(self, language: str, code: str, stdin: str = "") -> Execution:
        runtime, filename = LANGUAGES[language]
        payload = {
            "language": runtime,
            "version": self._version,
            "files": [{"name": filename, "content": code}],
            "stdin": stdin,
            "compile_timeout": COMPILE_TIMEOUT_MS,
            "run_timeout": RUN_TIMEOUT_MS,
            "compile_memory_limit": MEMORY_LIMIT,
            "run_memory_limit": MEMORY_LIMIT,
        }
        try:
            response = await self._http.post(self._url, json=payload, timeout=30)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PistonUnavailableError("The code runner is not available right now.") from exc
        compile_stage = body.get("compile") or {}
        run = body.get("run") or {}
        compile_failed = bool(compile_stage) and compile_stage.get("code") not in (0, None)
        return Execution(
            stdout=str(run.get("stdout", ""))[:OUTPUT_LIMIT],
            stderr=str(run.get("stderr", ""))[:OUTPUT_LIMIT],
            exit_code=run.get("code"),
            signal=run.get("signal"),
            compile_output=str(compile_stage.get("output") or compile_stage.get("stderr") or "")[:OUTPUT_LIMIT],
            compile_failed=compile_failed,
        )

    async def check(self, task: PracticalTask, code: str) -> RunReport:
        """Compile with warnings and run every test case of the task."""
        source = with_warnings(code, task.language)
        first = await self.execute(task.language, source, task.tests[0].stdin if task.tests else "")
        warnings, errors = diagnostics(first.compile_output)
        linkable = not _LINK_ERROR.search(first.compile_output)
        report = RunReport(
            compiled=not first.compile_failed or (not errors and not linkable), warnings=warnings, errors=errors
        )
        if first.compile_failed or not task.tests:
            return report
        for index, test in enumerate(task.tests):
            result = first if index == 0 else await self.execute(task.language, source, test.stdin)
            detail = (
                f"killed by {result.signal}" if result.signal else (result.stderr[:300] if result.exit_code else "")
            )
            report.tests.append(
                TestOutcome(
                    stdin=test.stdin,
                    expected=test.expected_stdout,
                    actual=result.stdout[:2000],
                    passed=same_output(result.stdout, test.expected_stdout) and not result.signal,
                    detail=detail,
                )
            )
        return report
