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
