"""Case memory (SPEC 4.6): experience, filled by the harness, never by hand.

A case = the symptom signature of a solved task + the abstracted hypothesis that solved it
+ the chain of operations. Retrieval is symbolic (weighted match of signature fields).

Recording happens only for train runs (the caller decides; test never records). During a
recording run nothing is retrieved, so a memory is frozen before it is used.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

from harness.contracts import TestRun

# Weights of signature fields in the similarity score (sum = 1).
WEIGHTS = {"exc_type": 0.30, "kind": 0.20, "frames": 0.30, "n_failing": 0.10, "multi_module": 0.10}
_KEYWORDS = {"def", "return", "if", "else", "for", "in", "not", "and", "or", "is", "None", "True", "False",
             "assert", "raise", "File", "line", "N", "NUM", "STR", "ADDR", "where", "self", "len", "with",
             "lambda", "while", "except", "try", "import", "from", "E"}


def _exc_type(t: TestRun) -> str:
    for line in reversed(t.traceback.splitlines()):
        m = re.match(r"^E?\s*([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning))\b", line.strip())
        if m:
            return m.group(1).split(".")[-1]
    if t.first_message:
        m = re.match(r"([A-Za-z_]\w*(?:Error|Exception))", t.first_message)
        if m:
            return m.group(1)
    return "AssertionError" if "assert" in t.traceback else "unknown"


def _kind(t: TestRun, exc: str) -> str:
    """exception / assertion / timeout / wrong_value (SPEC 4.6)."""
    if t.timed_out:
        return "timeout"
    if exc == "AssertionError":
        tb = t.traceback
        return "wrong_value" if re.search(r"assert .*(==|!=|<|>| in )", tb) or "where" in tb else "assertion"
    return "exception"


def _frames(sig: str) -> list[str]:
    """Normalized traceback shape: file names and statement structure, with names replaced by
    a placeholder. Variable names and literals are gone (normalize_traceback did literals)."""
    out = []
    for line in sig.splitlines():
        if line.startswith("File "):
            out.append("FRAME")   # file and function names are task-specific: only the depth counts
            continue
        toks = re.findall(r"[A-Za-z_]\w*|==|!=|<=|>=|[<>+\-*/%\[\]().,:]", line)
        shape = " ".join(tok if tok in _KEYWORDS or not re.match(r"[A-Za-z_]", tok) else "ID" for tok in toks)
        if shape:
            out.append(shape)
    return out


def signature(t: TestRun, source_files: list[str] | None = None) -> dict:
    exc = _exc_type(t)
    files = {Path(m).name for m in re.findall(r'File "([^"]+\.py)"', t.traceback)}
    if source_files:
        src = {Path(f).name for f in source_files}
        files &= src
    return {
        "exc_type": exc,
        "kind": _kind(t, exc),
        "frames": _frames(t.signature or t.traceback)[:30],
        "n_failing": t.n_failing,
        "multi_module": len(files) > 1,
    }


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa | sb else 1.0


def similarity(a: dict, b: dict) -> float:
    s = WEIGHTS["exc_type"] * (a["exc_type"] == b["exc_type"])
    s += WEIGHTS["kind"] * (a["kind"] == b["kind"])
    s += WEIGHTS["frames"] * _jaccard(a["frames"], b["frames"])
    na, nb = a["n_failing"], b["n_failing"]
    s += WEIGHTS["n_failing"] * (1 - abs(na - nb) / max(na, nb, 1))
    s += WEIGHTS["multi_module"] * (a["multi_module"] == b["multi_module"])
    return round(s, 4)


class CaseMemory:
    """memory/<name>.jsonl. Thread-safe append; retrieval over the cases loaded at start."""

    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self.cases: list[dict] = []
        if self.path and self.path.exists():
            self.cases = [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]

    def version(self) -> str:
        if not self.path or not self.path.exists():
            return "none"
        return f"{self.path.stem}@{hashlib.sha256(self.path.read_bytes()).hexdigest()[:10]}"

    def retrieve(self, sig: dict, m: int = 3, exclude: set[str] = frozenset(),
                 exclude_task: str | None = None) -> list[tuple[float, dict]]:
        """Top-m cases by similarity; at most one case per source task (seeds of one task
        would otherwise crowd out everything else)."""
        best: dict[str, tuple[float, dict]] = {}
        for c in self.cases:
            if c["case_id"] in exclude or c["source_task"] == exclude_task:
                continue
            s = similarity(sig, c["signature"])
            cur = best.get(c["source_task"])
            if cur is None or s > cur[0] or (s == cur[0] and c["steps"] < cur[1]["steps"]):
                best[c["source_task"]] = (s, c)
        return sorted(best.values(), key=lambda x: (-x[0], x[1]["case_id"]))[:m]

    def add(self, case: dict) -> None:
        if self.path is None:
            return
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")


def case_id(source_task: str, seed, model: str) -> str:
    return hashlib.sha256(f"{source_task}|{model}|{seed}".encode()).hexdigest()[:12]


def render_case(score: float, c: dict) -> str:
    h = c["hit_hypothesis"]
    return (f"A past case with a similar symptom (similarity {score:.2f}; {c['signature']['exc_type']}, "
            f"{c['signature']['kind']}): the cause was in category '{h['category']}': {h['claim_abstract']} "
            f"It was solved by: {' -> '.join(c['operation_chain'])}.")


# --- leak check (SPEC 4.6: claim_abstract must not contain task identifiers) ---------------

_LEAK = None
_ALL_IDENTS: set[str] | None = None


def _leak_module():
    global _LEAK
    if _LEAK is None:
        import importlib.util
        p = Path(__file__).resolve().parent.parent / "tools" / "leak_check.py"
        spec = importlib.util.spec_from_file_location("_impasse_leak_check", p)
        _LEAK = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_LEAK)
    return _LEAK


def leak_identifiers(source: str) -> set[str]:
    return _leak_module().identifiers(source)


def leaks(text: str, own: set[str]) -> list[str]:
    """Identifiers of the source task, or of any benchmark task, found in text."""
    global _ALL_IDENTS
    if _ALL_IDENTS is None:
        try:
            _ALL_IDENTS = set(_leak_module().task_identifiers())
        except Exception:  # noqa: BLE001 — no bench available (unit tests): own identifiers only
            _ALL_IDENTS = set()
    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text))
    return sorted(words & (own | _ALL_IDENTS))
