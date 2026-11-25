"""Task workspace: deterministic test runs, file actions, sandbox, snapshots, diffs."""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from harness.contracts import TestRun

TRACEBACK_LINES = 40
READ_MAX_LINES = 200
SEARCH_MAX_HITS = 50
OUTPUT_MAX_CHARS = 4000
IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc")


class ActionRejected(Exception):
    """Action refused by the harness (bad path, tests/ edit, no match...)."""


def _have_unshare() -> bool:
    try:
        return subprocess.run(["unshare", "-rn", "true"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


NO_NET_PREFIX = ["unshare", "-rn"] if _have_unshare() else []


def normalize_traceback(tb: str) -> str:
    """Strip paths, line numbers, literals and long identifiers' specifics so that
    the 'same error' detector and case signatures ignore incidental differences."""
    s = re.sub(r'File "[^"]*[/\\]([^/\\"]+)"', r'File "\1"', tb)
    s = re.sub(r"line \d+", "line N", s)
    s = re.sub(r"0x[0-9a-fA-F]+", "ADDR", s)
    s = re.sub(r"'[^'\n]*'|\"[^\"\n]*\"", "STR", s)
    s = re.sub(r"\b\d+(\.\d+)?\b", "NUM", s)
    s = re.sub(r"[ \t]+", " ", s)
    return "\n".join(l.strip() for l in s.splitlines() if l.strip())


class Workspace:
    def __init__(self, root: Path, python: str = sys.executable, test_timeout: int = 60):
        self.root = Path(root).resolve()
        self.python = python
        self.test_timeout = test_timeout

    # --- setup / integrity ---------------------------------------------

    @classmethod
    def create(cls, task_dir: Path, dest: Path, **kw) -> "Workspace":
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(task_dir, dest, ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", "*.pyc", "solution.patch", "task.yaml"))
        return cls(dest, **kw)

    def tests_checksum(self) -> str:
        h = hashlib.sha256()
        tdir = self.root / "tests"
        for p in sorted(tdir.rglob("*")) if tdir.exists() else []:
            if p.is_file() and "__pycache__" not in p.parts:
                h.update(str(p.relative_to(self.root)).encode())
                h.update(p.read_bytes())
        return h.hexdigest()

    def source_files(self) -> dict[str, str]:
        out = {}
        for p in sorted(self.root.rglob("*.py")):
            rel = p.relative_to(self.root)
            if rel.parts[0] == "tests" or "__pycache__" in rel.parts:
                continue
            out[str(rel)] = p.read_text(errors="replace")
        return out

    def snapshot(self, dest: Path) -> Path:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(self.root, dest, ignore=IGNORE)
        return dest

    def restore(self, snap: Path) -> None:
        for p in self.root.iterdir():
            if p.name == "tests":
                continue
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        for p in snap.iterdir():
            if p.name == "tests":
                continue
            (shutil.copytree(p, self.root / p.name) if p.is_dir() else shutil.copy2(p, self.root / p.name))

    @staticmethod
    def diff(before: dict[str, str], after: dict[str, str]) -> str:
        chunks = []
        for name in sorted(set(before) | set(after)):
            a, b = before.get(name, ""), after.get(name, "")
            if a != b:
                chunks.extend(difflib.unified_diff(a.splitlines(True), b.splitlines(True),
                                                   f"a/{name}", f"b/{name}", n=2))
        return "".join(chunks)

    # --- path safety -----------------------------------------------------

    def _resolve(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise ActionRejected(f"path outside the task: {rel}")
        return p

    # --- deterministic TEST_RUN -----------------------------------------

    def run_tests(self, select: list[str] | None = None) -> TestRun:
        with tempfile.TemporaryDirectory() as td:
            xml = Path(td) / "r.xml"
            cmd = [*NO_NET_PREFIX, self.python, "-m", "pytest", "-q",
                   "-p", "no:cacheprovider", "--tb=long", f"--junitxml={xml}", *(select or [])]
            try:
                proc = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True,
                                      timeout=self.test_timeout, env=self._env())
            except subprocess.TimeoutExpired:
                return TestRun(timed_out=True, traceback="pytest timed out",
                               signature="TIMEOUT")
            return self._parse(xml, proc.stdout + proc.stderr)

    def _parse(self, xml: Path, output: str) -> TestRun:
        if not xml.exists():
            tb = "\n".join(output.splitlines()[-TRACEBACK_LINES:])
            return TestRun(collection_error=True, traceback=tb, signature=normalize_traceback(tb))
        root = ET.parse(xml).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        res = TestRun()
        for s in suites:
            res.errors += int(s.get("errors", 0))
            res.failed += int(s.get("failures", 0))
            res.passed += int(s.get("tests", 0)) - int(s.get("errors", 0)) - int(s.get("failures", 0)) - int(s.get("skipped", 0))
        for case in root.iter("testcase"):
            bad = case.find("failure")
            if bad is None:
                bad = case.find("error")
            if bad is None:
                continue
            node = f"{case.get('classname', '')}::{case.get('name', '')}"
            text = bad.text or ""
            lines = text.splitlines()
            res.first_failure = node
            res.first_message = (bad.get("message") or "")[:300]
            res.traceback = "\n".join(lines[-TRACEBACK_LINES:])
            res.signature = normalize_traceback(
                "\n".join(l for l in lines if l.startswith(("E ", "E\t")) or "Error" in l)[-2000:])
            break
        if res.passed + res.n_failing == 0 and "error" in output.lower():
            res.collection_error = True
            res.traceback = "\n".join(output.splitlines()[-TRACEBACK_LINES:])
            res.signature = normalize_traceback(res.traceback)
        return res

    def _env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("HTTP", "HTTPS", "OPENAI"))}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONHASHSEED"] = "0"
        return env

    # --- subagent actions (SPEC 3.3) ------------------------------------

    def read_file(self, path: str, start: str | None = None, end: str | None = None) -> str:
        p = self._resolve(path)
        if not p.is_file():
            raise ActionRejected(f"no such file: {path}")
        lines = p.read_text(errors="replace").splitlines()
        a = max(1, int(start)) if start else 1
        b = min(len(lines), int(end)) if end else len(lines)
        b = min(b, a + READ_MAX_LINES - 1)
        body = "\n".join(f"{i:4d}| {lines[i-1]}" for i in range(a, b + 1))
        more = f"\n... ({len(lines)} lines total)" if b < len(lines) else ""
        return f"{path} lines {a}-{b}:\n{body}{more}"

    def search(self, pattern: str, path: str | None = None) -> str:
        try:
            rx = re.compile(pattern)
        except re.error:
            rx = re.compile(re.escape(pattern))
        base = self._resolve(path) if path else self.root
        files = [base] if base.is_file() else sorted(base.rglob("*.py"))
        hits = []
        for f in files:
            if "__pycache__" in f.parts:
                continue
            for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f.relative_to(self.root)}:{i}: {line.strip()}")
                    if len(hits) >= SEARCH_MAX_HITS:
                        return "\n".join(hits) + "\n... (truncated)"
        return "\n".join(hits) or "no matches"

    def apply_patch(self, path: str, old: str, new: str) -> str:
        p = self._resolve(path)
        rel = p.relative_to(self.root)
        if rel.parts and rel.parts[0] == "tests":
            raise ActionRejected("editing files under tests/ is forbidden")
        if not p.is_file():
            raise ActionRejected(f"no such file: {path}")
        text = p.read_text()
        if not old:
            raise ActionRejected("'old' must be a non-empty exact fragment of the file")
        n = text.count(old)
        if n == 0:
            raise ActionRejected("'old' text not found (must match exactly, including indentation)")
        if n > 1:
            raise ActionRejected(f"'old' text matches {n} places; include more context")
        p.write_text(text.replace(old, new, 1))
        return f"patched {path}"

    def run_python(self, code: str, timeout: int = 10) -> str:
        cmd = [*NO_NET_PREFIX, self.python, "-I", "-c", "import sys; sys.path.insert(0, '.')\n" + code]
        try:
            proc = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True,
                                  timeout=timeout, env=self._env())
        except subprocess.TimeoutExpired:
            return f"timeout after {timeout}s"
        out = f"exit={proc.returncode}\n{proc.stdout}{proc.stderr}"
        return out[:OUTPUT_MAX_CHARS]
