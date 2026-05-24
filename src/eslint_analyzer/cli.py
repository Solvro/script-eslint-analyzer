from __future__ import annotations

import csv
import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import click
from loguru import logger


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
class Repo:
    org: str
    name: str
    default_branch: str

    @property
    def full_name(self) -> str:
        return f"{self.org}/{self.name}"


def setup_logging() -> None:
    logger.remove()
    logger.add(
        sink=click.echo,
        format="<level>{level: <7}</level> | {message}",
        colorize=True,
    )


def run_cmd(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def discover_repos(org: str) -> list[Repo]:
    logger.info("Discovering repositories for org {}", org)
    try:
        proc = run_cmd(["gh", "api", f"/orgs/{org}/repos", "--paginate"])
    except FileNotFoundError as exc:
        raise click.ClickException("Missing `gh` CLI in PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(f"Failed to query GitHub: {exc.stderr.strip()}") from exc

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise click.ClickException("Malformed JSON output from `gh api`.") from exc

    if not isinstance(payload, list):
        raise click.ClickException("Unexpected `gh api` payload; expected list.")

    repos: list[Repo] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
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


def is_git_repo(path: Path) -> bool:
    try:
        proc = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=path)
        return proc.stdout.strip() == "true"
    except subprocess.CalledProcessError:
        return False


def repo_dirty(path: Path) -> bool:
    proc = run_cmd(["git", "status", "--porcelain"], cwd=path)
    return bool(proc.stdout.strip())


def has_local_branch(path: Path, branch: str) -> bool:
    proc = run_cmd(["git", "branch", "--list", branch], cwd=path)
    return bool(proc.stdout.strip())


def sync_repo(repo: Repo, root_dir: Path) -> tuple[bool, str | None]:
    repo_path = root_dir / repo.name

    if not repo_path.exists():
        logger.info("Cloning {}", repo.full_name)
        try:
            run_cmd(["gh", "repo", "clone", repo.full_name, str(repo_path)])
            return True, None
        except subprocess.CalledProcessError as exc:
            return False, f"clone failed: {exc.stderr.strip()}"

    if not is_git_repo(repo_path):
        return False, "path exists but is not a git repository"

    try:
        run_cmd(["git", "fetch", "origin"], cwd=repo_path)
    except subprocess.CalledProcessError as exc:
        return False, f"fetch failed: {exc.stderr.strip()}"

    local_ref = f"refs/heads/{repo.default_branch}"
    remote_ref = f"refs/remotes/origin/{repo.default_branch}"
    local_sha = run_cmd(["git", "rev-parse", local_ref], cwd=repo_path, check=False).stdout.strip()
    remote_sha = run_cmd(["git", "rev-parse", remote_ref], cwd=repo_path, check=False).stdout.strip()

    if not remote_sha:
        return False, f"missing remote branch origin/{repo.default_branch}"

    if local_sha == remote_sha and local_sha:
        return True, None

    if repo_dirty(repo_path):
        return False, "repo is dirty and behind remote"

    try:
        if has_local_branch(repo_path, repo.default_branch):
            run_cmd(["git", "checkout", repo.default_branch], cwd=repo_path)
        else:
            run_cmd(["git", "checkout", "-b", repo.default_branch, "--track", f"origin/{repo.default_branch}"], cwd=repo_path)
        run_cmd(["git", "pull", "--ff-only", "origin", repo.default_branch], cwd=repo_path)
        return True, None
    except subprocess.CalledProcessError as exc:
        return False, f"sync failed: {exc.stderr.strip()}"


def iter_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def parse_rules(raw_rules: str) -> list[str]:
    cleaned = raw_rules.split("--", 1)[0]
    tokens = [token.strip(" ,") for token in RULE_TOKEN_RE.findall(cleaned)]
    rules = [t for t in tokens if t and not t.lower().startswith("eslint-disable")]
    return rules or ["__all_rules__"]


def scan_repo(repo: Repo, repo_path: Path, counter: Counter[str], rule_to_repos: dict[str, set[str]]) -> None:
    for file_path in iter_files(repo_path):
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for match in DISABLE_DIRECTIVE_RE.finditer(text):
            rules = parse_rules(match.group("rules") or "")
            for rule in rules:
                counter[rule] += 1
                rule_to_repos[rule].add(repo.full_name)


def export_results(output: Path, fmt: str, counter: Counter[str], rule_to_repos: dict[str, set[str]]) -> None:
    delimiter = "\t" if fmt == "tsv" else ","
    rows = sorted(counter.items(), key=lambda item: (-item[1], item[0]))

    with output.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.writer(fp, delimiter=delimiter)
        writer.writerow(["rule", "count", "repositories"])
        for rule, count in rows:
            repos = ",".join(sorted(rule_to_repos[rule]))
            writer.writerow([rule, count, repos])


def build_summary(counter: Counter[str], rule_to_repos: dict[str, set[str]], analyzed: int, skipped: int) -> str:
    lines = [
        "## Latest Findings",
        "",
        f"Analyzed repositories: **{analyzed}**",
        f"Skipped repositories: **{skipped}**",
        f"Total ESLint disable directives found: **{sum(counter.values())}**",
        f"Unique ignored rules: **{len(counter)}**",
        "",
        "### Top 10 Ignored Rules",
        "",
    ]

    if not counter:
        lines.append("No ESLint disable directives were found.")
        return "\n".join(lines) + "\n"

    lines.extend([
        "| Rule | Count | Repositories |",
        "| --- | ---: | --- |",
    ])
    for rule, count in counter.most_common(10):
        repos_list = ", ".join(sorted(rule_to_repos[rule]))
        lines.append(f"| `{rule}` | {count} | {repos_list} |")

    return "\n".join(lines) + "\n"


def export_summary(output: Path, counter: Counter[str], rule_to_repos: dict[str, set[str]], analyzed: int, skipped: int) -> None:
    output.write_text(build_summary(counter, rule_to_repos, analyzed, skipped), encoding="utf-8")


@click.command()
@click.option("--org", default="Solvro", show_default=True, help="GitHub organization")
@click.option("--root-dir", default="~/repos", show_default=True, type=click.Path(path_type=Path), help="Directory for local clones")
@click.option("--output", default="result.tsv", show_default=True, type=click.Path(path_type=Path), help="Report file path")
@click.option("--summary-output", type=click.Path(path_type=Path), help="Markdown summary file path")
@click.option("--format", "output_format", type=click.Choice(["tsv", "csv"]), default="tsv", show_default=True, help="Output format")
def main(org: str, root_dir: Path, output: Path, summary_output: Path | None, output_format: str) -> None:
    setup_logging()

    root_dir = root_dir.expanduser().resolve()
    output = output.expanduser().resolve()
    if summary_output is not None:
        summary_output = summary_output.expanduser().resolve()

    root_dir.mkdir(parents=True, exist_ok=True)

    repos = discover_repos(org)

    counter: Counter[str] = Counter()
    rule_to_repos: dict[str, set[str]] = defaultdict(set)
    analyzed = 0
    skipped = 0

    for repo in repos:
        ok, reason = sync_repo(repo, root_dir)
        if not ok:
            skipped += 1
            logger.error("Skipping {}: {}", repo.full_name, reason)
            continue

        try:
            scan_repo(repo, root_dir / repo.name, counter, rule_to_repos)
            analyzed += 1
            logger.info("Analyzed {}", repo.full_name)
        except Exception as exc:
            skipped += 1
            logger.error("Failed {}: {}", repo.full_name, exc)

    export_results(output, output_format, counter, rule_to_repos)
    logger.success("Saved report to {}", output)
    if summary_output is not None:
        export_summary(summary_output, counter, rule_to_repos, analyzed, skipped)
        logger.success("Saved summary to {}", summary_output)
    logger.info("Analyzed repositories: {}", analyzed)
    logger.warning("Skipped repositories: {}", skipped)

    click.echo("\nTop 10 ignored rules:")
    for idx, (rule, count) in enumerate(counter.most_common(10), start=1):
        repos_list = ", ".join(sorted(rule_to_repos[rule]))
        click.secho(f"{idx:>2}. {rule}", fg="cyan", bold=True, nl=False)
        click.echo(f" -> {count} ({repos_list})")


if __name__ == "__main__":
    main()
