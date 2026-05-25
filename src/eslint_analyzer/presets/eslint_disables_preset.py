from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
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
    @staticmethod
    def escape_markdown(text: str) -> str:
        escaped = text.replace("\\", "\\\\")
        for char in ("*", "_", "`", "[", "]", "(", ")", "|", "#"):
            escaped = escaped.replace(char, f"\\{char}")
        return escaped

    @staticmethod
    def occurrence_url(occurrence: Occurrence) -> str:
        return (
            f"https://github.com/{occurrence.repo}/blob/{occurrence.commit_sha}/"
            f"{occurrence.file_path}#L{occurrence.line}"
        )

    @staticmethod
    def occurrence_label(occurrence: Occurrence) -> str:
        raw = f"{occurrence.file_path}:{occurrence.line}"
        return EslintDisablesPreset.escape_markdown(raw)

    @staticmethod
    def occurrence_label_with_repo(occurrence: Occurrence) -> str:
        short_sha = occurrence.commit_sha[:7]
        raw = f"{occurrence.repo}@{short_sha} {occurrence.file_path}:{occurrence.line}"
        return EslintDisablesPreset.escape_markdown(raw)

    @classmethod
    def build_occurrence_details(
        cls,
        occurrences: list[Occurrence],
        summary: int,
        include_repo_context: bool,
    ) -> str:
        lines = [f"<details><summary>{summary}</summary><ul>"]
        for occurrence in occurrences:
            url = cls.occurrence_url(occurrence)
            label = (
                cls.occurrence_label_with_repo(occurrence)
                if include_repo_context
                else cls.occurrence_label(occurrence)
            )
            lines.append(f'<li><a href="{url}">{label}</a></li>')
        lines.append("</ul></details>")
        return "".join(lines)

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
        repo_disable_count = 0
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
                    repo_disable_count += 1
                    rule_to_repos[rule].add(self.repo.full_name)
                    rule_to_occurrences[rule].append(
                        Occurrence(self.repo.full_name, commit_sha, relative_path, line)
                    )

        return {
            "mode": "eslint-disable",
            "counter": counter,
            "rule_to_repos": rule_to_repos,
            "rule_to_occurrences": rule_to_occurrences,
            "repo_disable_count": repo_disable_count,
        }

    def generate_markdown(self, result: dict) -> str:
        generated_at = result.get("generated_at") or datetime.now(UTC).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
        analyzed = int(result.get("analyzed", 0))
        skipped = int(result.get("skipped", 0))
        counter: Counter[str] = result.get("counter", Counter())
        rule_to_repos: dict[str, set[str]] = result.get("rule_to_repos", {})
        rule_to_occurrences: dict[str, list[Occurrence]] = result.get(
            "rule_to_occurrences", {}
        )
        repo_totals: dict[str, int] = result.get("repo_totals", {})
        repo_occurrences: dict[str, list[Occurrence]] = defaultdict(list)

        for occurrences in rule_to_occurrences.values():
            for occ in occurrences:
                repo_occurrences[occ.repo].append(occ)

        lines = [
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Last updated | **{generated_at}** |",
            f"| Analyzed repositories | **{analyzed}** |",
            f"| Skipped repositories | **{'None' if skipped == 0 else skipped}** |",
            f"| Total ESLint disable directives found | **{sum(counter.values())}** |",
            f"| Unique ignored rules | **{len(counter)}** |",
            "",
        ]

        if repo_totals:
            lines.extend(
                [
                    "### Most Cursed Codebases",
                    "",
                    "| Repository | Ignores |",
                    "| --- | --- |",
                ]
            )
            for repo, total in sorted(
                repo_totals.items(), key=lambda item: (-item[1], item[0])
            )[:5]:
                occurrences = sorted(
                    repo_occurrences.get(repo, []),
                    key=lambda occ: (occ.file_path, occ.line),
                )
                details = self.build_occurrence_details(
                    occurrences,
                    total,
                    include_repo_context=False,
                )
                lines.append(f"| [{repo}](https://github.com/{repo}) | {details} |")
            lines.append("")

        lines.extend(
            [
                "### Top 10 Ignored Rules",
                "",
                "| Rule | Count | Repositories |",
                "| --- | --- | --- |",
            ]
        )

        for rule, count in counter.most_common(10):
            occurrences = sorted(
                rule_to_occurrences.get(rule, []),
                key=lambda occ: (occ.repo, occ.file_path, occ.line),
            )
            details = self.build_occurrence_details(
                occurrences,
                count,
                include_repo_context=True,
            )
            repo_links = ", ".join(
                f"[{repo}](https://github.com/{repo})"
                for repo in sorted(rule_to_repos.get(rule, set()))
            )
            lines.append(f"| `{rule}` | {details} | {repo_links} |")

        return "\n".join(lines).rstrip() + "\n"
