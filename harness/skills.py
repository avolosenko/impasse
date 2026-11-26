"""Skill library: SKILL.md with YAML front matter (SPEC 4.1). Loaded by triggers in code."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Skill:
    name: str
    phase: str
    kind: str
    triggers: list[str]
    applies_to_operation: list[str]
    body: str
    version: int = 1
    uses: list[str] = field(default_factory=list)


def parse_skill(text: str) -> Skill:
    if not text.startswith("---"):
        raise ValueError("SKILL.md must start with YAML front matter")
    _, fm, body = text.split("---", 2)
    meta = yaml.safe_load(fm) or {}
    return Skill(name=meta["name"], phase=meta.get("phase", "operate"), kind=meta.get("kind", "procedure"),
                 triggers=list(meta.get("triggers") or []),
                 applies_to_operation=list(meta.get("applies_to_operation") or []),
                 body=body.strip(), version=int(meta.get("version", 1)), uses=list(meta.get("uses") or []))


class SkillLibrary:
    MAX_IN_CONTEXT = 2

    def __init__(self, root: Path | None):
        self.skills: dict[str, Skill] = {}
        if root and Path(root).exists():
            for f in sorted(Path(root).glob("*/SKILL.md")):
                if f.parent.name.startswith("_"):
                    continue  # _meta, _proposals are not runtime skills
                s = parse_skill(f.read_text())
                self.skills[s.name] = s

    def for_operation(self, phase: str, operation: str | None) -> list[Skill]:
        return [s for s in self.skills.values()
                if s.phase == phase and operation and operation in s.applies_to_operation]

    def for_trigger(self, trigger: str) -> list[Skill]:
        return [s for s in self.skills.values() if trigger in s.triggers]

    def select(self, phase: str, operation: str | None, pending_triggers: list[str]) -> list[Skill]:
        """Trigger skills first (they react to a detected loop), then operation skills; max 2."""
        out: list[Skill] = []
        for t in pending_triggers:
            for s in self.for_trigger(t):
                if s not in out and s.phase in (phase, "any"):
                    out.append(s)
        for s in self.for_operation(phase, operation):
            if s not in out:
                out.append(s)
        return out[: self.MAX_IN_CONTEXT]


def render(skills: list[Skill]) -> str:
    return "\n\n".join(f"### Skill: {s.name}\n{s.body}" for s in skills)
