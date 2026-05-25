from __future__ import annotations

import csv
import json
import subprocess
import shutil
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import click
from loguru import logger

from eslint_analyzer.presets.base_preset import BasePreset, Repo
from eslint_analyzer.presets.eslint_disables_preset import (
    EslintDisablesPreset,
    Occurrence,
)
from eslint_analyzer.presets.eslint_errors_preset import EslintErrorsPreset


@dataclass
class RepoAnalysisResult:
    repo: Repo
    ok: bool
    reason: str | None
    payload: dict


class Analysis:
    @staticmethod
    def setup_logging() -> None:
        logger.remove()
        logger.add(
            sink=click.echo,
            format="<level>{level: <7}</level> | {message}",
            colorize=True,
        )

    @staticmethod
    def run_cmd(args: list[str]) -> str:
        return BasePreset.run_cmd(args).stdout

    @staticmethod
    def discover_repos(org: str) -> list[Repo]:
        logger.info("Discovering repositories for org {}", org)
        try:
            output = Analysis.run_cmd(
                ["gh", "api", f"/orgs/{org}/repos?type=public", "--paginate"]
            )
        except FileNotFoundError as exc:
            raise click.ClickException("Missing `gh` CLI in PATH.") from exc

        payload = json.loads(output)
        repos: list[Repo] = []
        for item in payload:
            name = item.get("name")
            default_branch = item.get("default_branch")
            archived = bool(item.get("archived"))
            fork = bool(item.get("fork"))
            if not isinstance(name, str) or not isinstance(default_branch, str):
                continue
            if archived or fork:
                continue
            if not (name.startswith("web-") or name.startswith("backend-")):
                continue
            repos.append(Repo(org=org, name=name, default_branch=default_branch))
        logger.success("Selected {} repositories", len(repos))
        return repos

    @staticmethod
    def build_summary(counter: Counter[str], analyzed: int, skipped: int) -> str:
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Last updated | **{now}** |",
            f"| Analyzed repositories | **{analyzed}** |",
            f"| Skipped repositories | **{'None' if skipped == 0 else skipped}** |",
            f"| Total ESLint disable directives found | **{sum(counter.values())}** |",
            f"| Unique ignored rules | **{len(counter)}** |",
            "",
        ]
        return "\n".join(lines) + "\n"

    @staticmethod
    def format_exception(exc: Exception) -> str:
        if isinstance(exc, subprocess.CalledProcessError):
            parts = [f"{exc.__class__.__name__}: command failed"]
            if exc.cmd:
                if isinstance(exc.cmd, list):
                    parts.append(f"cmd={' '.join(exc.cmd)}")
                else:
                    parts.append(f"cmd={exc.cmd}")
            if exc.returncode is not None:
                parts.append(f"exit_code={exc.returncode}")
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            if stderr:
                parts.append(f"stderr={stderr}")
            elif stdout:
                parts.append(f"stdout={stdout}")
            return " | ".join(parts)

        message = str(exc).strip()
        if message:
            return f"{exc.__class__.__name__}: {message}"
        return exc.__class__.__name__

    @staticmethod
    def analyze_repo(
        repo: Repo,
        root_dir: Path,
        cleanup_cloned_repo: bool,
        preset_name: str,
    ) -> RepoAnalysisResult:
        repo_path = root_dir / repo.name
        existed_before = repo_path.exists()

        preset: BasePreset
        if preset_name == "eslint-disable":
            preset = EslintDisablesPreset(repo, root_dir)
        elif preset_name == "eslint-errors":
            preset = EslintErrorsPreset(repo, root_dir)
        else:
            return RepoAnalysisResult(
                repo=repo, ok=False, reason=f"Unknown preset: {preset_name}", payload={}
            )

        try:
            ok, reason = preset.prepare_repo()
            if not ok:
                return RepoAnalysisResult(
                    repo=repo, ok=False, reason=reason, payload={}
                )
            return RepoAnalysisResult(
                repo=repo, ok=True, reason=None, payload=preset.analyze()
            )
        except Exception as exc:
            return RepoAnalysisResult(
                repo=repo, ok=False, reason=Analysis.format_exception(exc), payload={}
            )
        finally:
            if cleanup_cloned_repo and not existed_before and repo_path.exists():
                try:
                    shutil.rmtree(repo_path)
                except OSError as exc:
                    logger.warning(
                        "Failed to cleanup {}: {}",
                        repo_path,
                        Analysis.format_exception(exc),
                    )

    @staticmethod
    def export_disable_results(
        output: Path,
        fmt: str,
        counter: Counter[str],
        rule_to_repos: dict[str, set[str]],
    ) -> None:
        delimiter = "\t" if fmt == "tsv" else ","
        rows = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
        with output.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.writer(fp, delimiter=delimiter)
            writer.writerow(["rule", "count", "repositories"])
            for rule, count in rows:
                writer.writerow([rule, count, ",".join(sorted(rule_to_repos[rule]))])

    @staticmethod
    def export_errors_results(
        output: Path,
        aggregate_counts: dict[str, dict[str, int]],
        per_repo_counts: dict[str, dict[str, dict[str, int]]],
    ) -> None:
        rows = sorted(
            aggregate_counts.items(),
            key=lambda item: (-(item[1]["errors"] + item[1]["warnings"]), item[0]),
        )
        with output.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.writer(fp, delimiter="\t")
            writer.writerow(["rule", "errors", "warnings"])
            total_errors = 0
            total_warnings = 0
            for rule, counts in rows:
                total_errors += counts["errors"]
                total_warnings += counts["warnings"]
                writer.writerow([rule, counts["errors"], counts["warnings"]])
            writer.writerow(["total", total_errors, total_warnings])

        per_repo_output = output.with_name(f"{output.stem}.per_repo{output.suffix}")
        with per_repo_output.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.writer(fp, delimiter="\t")
            writer.writerow(["repo", "rule", "errors", "warnings"])
            for repo in sorted(per_repo_counts):
                repo_total_errors = 0
                repo_total_warnings = 0
                for rule, counts in sorted(
                    per_repo_counts[repo].items(),
                    key=lambda item: (
                        -(item[1]["errors"] + item[1]["warnings"]),
                        item[0],
                    ),
                ):
                    repo_total_errors += counts["errors"]
                    repo_total_warnings += counts["warnings"]
                    writer.writerow([repo, rule, counts["errors"], counts["warnings"]])
                writer.writerow([repo, "total", repo_total_errors, repo_total_warnings])

    @staticmethod
    def run(
        org: str,
        root_dir: Path,
        output: Path,
        summary_output: Path | None,
        output_format: str,
        cleanup_cloned_repo: bool,
        preset_name: str,
        jobs: int,
    ) -> None:
        Analysis.setup_logging()

        root_dir = root_dir.expanduser().resolve()
        output = output.expanduser().resolve()
        if summary_output is not None:
            summary_output = summary_output.expanduser().resolve()
        root_dir.mkdir(parents=True, exist_ok=True)

        repos = Analysis.discover_repos(org)
        analyzed = 0
        skipped = 0

        disable_counter: Counter[str] = Counter()
        disable_rule_to_repos: dict[str, set[str]] = defaultdict(set)
        disable_rule_to_occurrences: dict[str, list[Occurrence]] = defaultdict(list)

        aggregate_counts: dict[str, dict[str, int]] = defaultdict(
            lambda: {"errors": 0, "warnings": 0}
        )
        per_repo_counts: dict[str, dict[str, dict[str, int]]] = defaultdict(
            lambda: defaultdict(lambda: {"errors": 0, "warnings": 0})
        )

        with ThreadPoolExecutor(max_workers=jobs) as executor:
            futures = [
                executor.submit(
                    Analysis.analyze_repo,
                    repo,
                    root_dir,
                    cleanup_cloned_repo,
                    preset_name,
                )
                for repo in repos
            ]
            for future in as_completed(futures):
                result = future.result()
                if not result.ok:
                    skipped += 1
                    logger.error(
                        "Skipping {}: {}", result.repo.full_name, result.reason
                    )
                    continue

                analyzed += 1
                logger.info("Analyzed {}", result.repo.full_name)
                if result.payload.get("mode") == "eslint-disable":
                    disable_counter.update(result.payload["counter"])
                    for rule, repos_set in result.payload["rule_to_repos"].items():
                        disable_rule_to_repos[rule].update(repos_set)
                    for rule, occurrences in result.payload[
                        "rule_to_occurrences"
                    ].items():
                        disable_rule_to_occurrences[rule].extend(occurrences)
                elif result.payload.get("mode") == "eslint-errors":
                    repo_name = result.payload["repo"]
                    for rule, counts in result.payload["counts"].items():
                        aggregate_counts[rule]["errors"] += counts["errors"]
                        aggregate_counts[rule]["warnings"] += counts["warnings"]
                        per_repo_counts[repo_name][rule]["errors"] += counts["errors"]
                        per_repo_counts[repo_name][rule]["warnings"] += counts[
                            "warnings"
                        ]

        if preset_name == "eslint-disable":
            Analysis.export_disable_results(
                output, output_format, disable_counter, disable_rule_to_repos
            )
            logger.success("Saved report to {}", output)
            if summary_output is not None:
                summary_output.write_text(
                    Analysis.build_summary(disable_counter, analyzed, skipped),
                    encoding="utf-8",
                )
                logger.success("Saved summary to {}", summary_output)
            click.echo("\nTop 10 ignored rules:")
            for idx, (rule, count) in enumerate(
                disable_counter.most_common(10), start=1
            ):
                click.secho(f"{idx:>2}. {rule}", fg="cyan", bold=True, nl=False)
                click.echo(f" -> {count}")
        else:
            Analysis.export_errors_results(output, aggregate_counts, per_repo_counts)
            logger.success("Saved report to {}", output)
            logger.success(
                "Saved per-repo report to {}",
                output.with_name(f"{output.stem}.per_repo{output.suffix}"),
            )
            click.echo("\nTop 10 rules by total diagnostics:")
            ranked = sorted(
                aggregate_counts.items(),
                key=lambda item: (-(item[1]["errors"] + item[1]["warnings"]), item[0]),
            )
            for idx, (rule, counts) in enumerate(ranked[:10], start=1):
                click.secho(f"{idx:>2}. {rule}", fg="cyan", bold=True, nl=False)
                click.echo(
                    f" -> errors={counts['errors']}, warnings={counts['warnings']}"
                )

        logger.info("Analyzed repositories: {}", analyzed)
        logger.warning("Skipped repositories: {}", skipped)
