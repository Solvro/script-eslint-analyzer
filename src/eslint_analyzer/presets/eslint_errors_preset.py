from __future__ import annotations

import json
from collections import defaultdict

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
        payload = json.loads(stdout) if stdout else []
        counts: dict[str, dict[str, int]] = defaultdict(
            lambda: {"errors": 0, "warnings": 0}
        )

        for file_report in payload:
            for message in file_report.get("messages", []):
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
