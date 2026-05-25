from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime

from eslint_analyzer.presets.base_preset import BasePreset


class EslintErrorsPreset(BasePreset):
    def analyze(self) -> dict:
        self.install_dependencies()
        package_manager = self.package_manager or self.detect_package_manager()

        if package_manager == "pnpm":
            proc = self.run_cmd(
                ["pnpm", "exec", "eslint", ".", "-f", "json"],
                cwd=self.repo_path,
                check=False,
            )
        elif package_manager == "npm":
            proc = self.run_cmd(
                ["npm", "exec", "eslint", ".", "-f", "json"],
                cwd=self.repo_path,
                check=False,
            )
        elif package_manager == "yarn":
            proc = self.run_cmd(
                ["yarn", "exec", "eslint", ".", "-f", "json"],
                cwd=self.repo_path,
                check=False,
            )
        else:
            raise RuntimeError(f"Unsupported package manager: {package_manager}")

        stdout = proc.stdout.strip()
        stderr = (proc.stderr or "").strip()

        if proc.returncode not in (0, 1):
            detail = stderr or stdout or f"eslint exited with code {proc.returncode}"
            raise RuntimeError(f"ESLint execution failed: {detail}")

        try:
            payload = json.loads(stdout) if stdout else []
        except json.JSONDecodeError as exc:
            detail = stderr or stdout or "missing eslint JSON output"
            raise RuntimeError(f"Failed to parse ESLint JSON output: {detail}") from exc

        if not isinstance(payload, list):
            raise RuntimeError("Unexpected ESLint JSON format: expected a list")
        counts: dict[str, dict[str, int]] = defaultdict(
            lambda: {"errors": 0, "warnings": 0}
        )

        for file_report in payload:
            if not isinstance(file_report, dict):
                continue
            for message in file_report.get("messages", []):
                if not isinstance(message, dict):
                    continue
                rule = message.get("ruleId") or "__unknown_rule__"
                severity = message.get("severity")
                if severity == 2:
                    counts[rule]["errors"] += 1
                elif severity == 1:
                    counts[rule]["warnings"] += 1

        return {
            "mode": "eslint-errors",
            "repo": self.repo.full_name,
            "counts": counts,
        }

    def generate_markdown(self, result: dict) -> str:
        generated_at = result.get("generated_at") or datetime.now(UTC).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
        analyzed = int(result.get("analyzed", 0))
        skipped = int(result.get("skipped", 0))
        aggregate_counts: dict[str, dict[str, int]] = result.get("aggregate_counts", {})
        per_repo_counts: dict[str, dict[str, dict[str, int]]] = result.get(
            "per_repo_counts", {}
        )

        total_errors = sum(v["errors"] for v in aggregate_counts.values())
        total_warnings = sum(v["warnings"] for v in aggregate_counts.values())
        total_diagnostics = total_errors + total_warnings

        lines = [
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Last updated | **{generated_at}** |",
            f"| Analyzed repositories | **{analyzed}** |",
            f"| Skipped repositories | **{'None' if skipped == 0 else skipped}** |",
            f"| Total diagnostics | **{total_diagnostics}** |",
            f"| Errors | **{total_errors}** |",
            f"| Warnings | **{total_warnings}** |",
            f"| Rules with diagnostics | **{len(aggregate_counts)}** |",
            "",
            "### Top 10 Rules by Diagnostics",
            "",
            "| Rule | Total | Errors | Warnings |",
            "| --- | ---: | ---: | ---: |",
        ]

        ranked_rules = sorted(
            aggregate_counts.items(),
            key=lambda item: (-(item[1]["errors"] + item[1]["warnings"]), item[0]),
        )
        for rule, counts in ranked_rules[:10]:
            total = counts["errors"] + counts["warnings"]
            lines.append(
                f"| `{rule}` | {total} | {counts['errors']} | {counts['warnings']} |"
            )

        lines.extend(
            [
                "",
                "### Most Affected Repositories",
                "",
                "| Repository | Total | Errors | Warnings |",
                "| --- | ---: | ---: | ---: |",
            ]
        )

        repo_rows: list[tuple[str, int, int, int]] = []
        for repo, rule_counts in per_repo_counts.items():
            repo_errors = sum(c["errors"] for c in rule_counts.values())
            repo_warnings = sum(c["warnings"] for c in rule_counts.values())
            repo_rows.append(
                (repo, repo_errors + repo_warnings, repo_errors, repo_warnings)
            )

        for repo, total, repo_errors, repo_warnings in sorted(
            repo_rows,
            key=lambda row: (-row[1], row[0]),
        )[:10]:
            repo_link = f"[{repo}](https://github.com/{repo})"
            lines.append(f"| {repo_link} | {total} | {repo_errors} | {repo_warnings} |")

        return "\n".join(lines).rstrip() + "\n"
