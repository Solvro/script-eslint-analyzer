from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from eslint_analyzer.presets.base_preset import BasePreset

SKIP_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    ".next",
    "coverage",
    ".cache",
    "out",
    "target",
    "vendor",
}

DISABLE_DIRECTIVE_RE = re.compile(
    r"eslint-disable(?:-next-line|-line|-file)?\b(?P<rules>[^\n*]*)",
    re.IGNORECASE,
)
RULE_TOKEN_RE = re.compile(r"[A-Za-z0-9@_./:-]+")


@dataclass(frozen=True)
class Occurrence:
    repo: str
    commit_sha: str
    file_path: str
    line: int


class EslintDisablesPreset(BasePreset):
    def current_commit_sha(self) -> str:
        return self.run_cmd(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_path
        ).stdout.strip()

    def iter_files(self, root: Path) -> Iterable[Path]:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            yield path

    def parse_rules(self, raw_rules: str) -> list[str]:
        cleaned = raw_rules.split("--", 1)[0]
        tokens = [token.strip(" ,") for token in RULE_TOKEN_RE.findall(cleaned)]
        rules = [t for t in tokens if t and not t.lower().startswith("eslint-disable")]
        return rules or ["__all_rules__"]

    def analyze(self) -> dict:
        counter: Counter[str] = Counter()
        rule_to_repos: dict[str, set[str]] = defaultdict(set)
        rule_to_occurrences: dict[str, list[Occurrence]] = defaultdict(list)
        commit_sha = self.current_commit_sha()

        for file_path in self.iter_files(self.repo_path):
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            for match in DISABLE_DIRECTIVE_RE.finditer(text):
                rules = self.parse_rules(match.group("rules") or "")
                line = text.count("\n", 0, match.start()) + 1
                relative_path = file_path.relative_to(self.repo_path).as_posix()
                for rule in rules:
                    counter[rule] += 1
                    rule_to_repos[rule].add(self.repo.full_name)
                    rule_to_occurrences[rule].append(
                        Occurrence(self.repo.full_name, commit_sha, relative_path, line)
                    )

        return {
            "mode": "eslint-disable",
            "counter": counter,
            "rule_to_repos": rule_to_repos,
            "rule_to_occurrences": rule_to_occurrences,
        }
