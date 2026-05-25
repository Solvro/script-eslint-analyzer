from __future__ import annotations

import csv
import html
import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

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
MARKDOWN_ESCAPE_RE = re.compile(r"([\\`*_{}\[\]<>()#+\-.!|])")


@dataclass(frozen=True)
class Repo:
    org: str
    name: str
    default_branch: str

    @property
    def full_name(self) -> str:
        return f"{self.org}/{self.name}"


@dataclass(frozen=True)
class Occurrence:
    repo: str
    commit_sha: str
    file_path: str
    line: int


@dataclass
class RepoAnalysisResult:
    repo: Repo
    ok: bool
    reason: str | None
    counter: Counter[str]
    rule_to_repos: dict[str, set[str]]
    rule_to_occurrences: dict[str, list[Occurrence]]


def setup_logging() -> None:
    logger.remove()
    logger.add(
        sink=click.echo,
        format="<level>{level: <7}</level> | {message}",
        colorize=True,
    )


def run_cmd(
    args: list[str], cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
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
        proc = run_cmd(["gh", "api", f"/orgs/{org}/repos?type=public", "--paginate"])
    except FileNotFoundError as exc:
        raise click.ClickException("Missing `gh` CLI in PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(
            f"Failed to query GitHub: {exc.stderr.strip()}"
        ) from exc

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


def current_commit_sha(path: Path) -> str:
    return run_cmd(["git", "rev-parse", "HEAD"], cwd=path).stdout.strip()


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
    local_sha = run_cmd(
        ["git", "rev-parse", local_ref], cwd=repo_path, check=False
    ).stdout.strip()
    remote_sha = run_cmd(
        ["git", "rev-parse", remote_ref], cwd=repo_path, check=False
    ).stdout.strip()

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
            run_cmd(
                [
                    "git",
                    "checkout",
                    "-b",
                    repo.default_branch,
                    "--track",
                    f"origin/{repo.default_branch}",
                ],
                cwd=repo_path,
            )
        run_cmd(
            ["git", "pull", "--ff-only", "origin", repo.default_branch], cwd=repo_path
        )
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


def scan_repo(
    repo: Repo,
    repo_path: Path,
    commit_sha: str,
    counter: Counter[str],
    rule_to_repos: dict[str, set[str]],
    rule_to_occurrences: dict[str, list[Occurrence]],
) -> None:
    for file_path in iter_files(repo_path):
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for match in DISABLE_DIRECTIVE_RE.finditer(text):
            rules = parse_rules(match.group("rules") or "")
            line = text.count("\n", 0, match.start()) + 1
            relative_path = file_path.relative_to(repo_path).as_posix()
            for rule in rules:
                counter[rule] += 1
                rule_to_repos[rule].add(repo.full_name)
                rule_to_occurrences[rule].append(
                    Occurrence(repo.full_name, commit_sha, relative_path, line)
                )


def export_results(
    output: Path, fmt: str, counter: Counter[str], rule_to_repos: dict[str, set[str]]
) -> None:
    delimiter = "\t" if fmt == "tsv" else ","
    rows = sorted(counter.items(), key=lambda item: (-item[1], item[0]))

    with output.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.writer(fp, delimiter=delimiter)
        writer.writerow(["rule", "count", "repositories"])
        for rule, count in rows:
            repos = ",".join(sorted(rule_to_repos[rule]))
            writer.writerow([rule, count, repos])


def github_blob_url(occurrence: Occurrence) -> str:
    quoted_path = quote(occurrence.file_path, safe="/")
    return f"https://github.com/{occurrence.repo}/blob/{occurrence.commit_sha}/{quoted_path}#L{occurrence.line}"


def escape_markdown(text: str) -> str:
    return MARKDOWN_ESCAPE_RE.sub(r"\\\1", text)


def build_count_details(occurrences: list[Occurrence]) -> str:
    items = []
    sorted_occurrences = sorted(
        occurrences,
        key=lambda item: f"{item.repo}/{item.file_path}:{item.line}".casefold(),
    )
    for occurrence in sorted_occurrences:
        label = html.escape(
            escape_markdown(
                f"{occurrence.repo}/{occurrence.file_path}:{occurrence.line}"
            )
        )
        items.append(f'<li><a href="{github_blob_url(occurrence)}">{label}</a></li>')

    return f"<details><summary>{len(occurrences)}</summary><ol>{''.join(items)}</ol></details>"


def build_repo_count_details(occurrences: list[Occurrence]) -> str:
    items = []
    sorted_occurrences = sorted(
        occurrences, key=lambda item: f"{item.file_path}:{item.line}".casefold()
    )
    for occurrence in sorted_occurrences:
        label = html.escape(
            escape_markdown(f"{occurrence.file_path}:{occurrence.line}")
        )
        items.append(f'<li><a href="{github_blob_url(occurrence)}">{label}</a></li>')

    return f"<details><summary>{len(occurrences)}</summary><ol>{''.join(items)}</ol></details>"


def build_summary(
    counter: Counter[str],
    rule_to_repos: dict[str, set[str]],
    rule_to_occurrences: dict[str, list[Occurrence]],
    analyzed: int,
    skipped: int,
) -> str:
    total_directives = sum(counter.values())
    repo_to_occurrences: dict[str, list[Occurrence]] = defaultdict(list)
    for occurrences in rule_to_occurrences.values():
        for occurrence in occurrences:
            repo_to_occurrences[occurrence.repo].append(occurrence)
    last_updated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Last updated | **{last_updated}** |",
        f"| Analyzed repositories | **{analyzed}** |",
        f"| Skipped repositories | **{'None' if skipped == 0 else skipped}** |",
        f"| Total ESLint disable directives found | **{total_directives}** |",
        f"| Unique ignored rules | **{len(counter)}** |",
        "",
    ]

    if not counter:
        lines.append("No ESLint disable directives were found.")
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "### Most Cursed Codebases",
            "",
            "| Repository | Ignores |",
            "| --- | --- |",
        ]
    )
    for repo, occurrences in sorted(
        repo_to_occurrences.items(), key=lambda item: (-len(item[1]), item[0])
    )[:5]:
        count_details = build_repo_count_details(occurrences)
        repo_label = escape_markdown(repo)
        lines.append(f"| [{repo_label}](https://github.com/{repo}) | {count_details} |")

    lines.extend(
        [
            "",
            "### Top 10 Ignored Rules",
            "",
        ]
    )

    lines.extend(
        [
            "| Rule | Count | Repositories |",
            "| --- | --- | --- |",
        ]
    )
    for rule, _ in counter.most_common(10):
        count_details = build_count_details(rule_to_occurrences[rule])
        repos_list = ", ".join(
            f"[{escape_markdown(repo)}](https://github.com/{repo})"
            for repo in sorted(rule_to_repos[rule])
        )
        lines.append(f"| {escape_markdown(rule)} | {count_details} | {repos_list} |")

    return "\n".join(lines) + "\n"


def export_summary(
    output: Path,
    counter: Counter[str],
    rule_to_repos: dict[str, set[str]],
    rule_to_occurrences: dict[str, list[Occurrence]],
    analyzed: int,
    skipped: int,
) -> None:
    output.write_text(
        build_summary(counter, rule_to_repos, rule_to_occurrences, analyzed, skipped),
        encoding="utf-8",
    )


def analyze_repo(
    repo: Repo,
    root_dir: Path,
    cleanup_cloned_repo: bool,
) -> RepoAnalysisResult:
    repo_path = root_dir / repo.name
    existed_before = repo_path.exists()

    counter: Counter[str] = Counter()
    rule_to_repos: dict[str, set[str]] = defaultdict(set)
    rule_to_occurrences: dict[str, list[Occurrence]] = defaultdict(list)

    try:
        ok, reason = sync_repo(repo, root_dir)
        if not ok:
            return RepoAnalysisResult(
                repo=repo,
                ok=False,
                reason=reason,
                counter=counter,
                rule_to_repos=rule_to_repos,
                rule_to_occurrences=rule_to_occurrences,
            )

        scan_repo(
            repo,
            repo_path,
            current_commit_sha(repo_path),
            counter,
            rule_to_repos,
            rule_to_occurrences,
        )
        return RepoAnalysisResult(
            repo=repo,
            ok=True,
            reason=None,
            counter=counter,
            rule_to_repos=rule_to_repos,
            rule_to_occurrences=rule_to_occurrences,
        )
    except Exception as exc:
        return RepoAnalysisResult(
            repo=repo,
            ok=False,
            reason=str(exc),
            counter=counter,
            rule_to_repos=rule_to_repos,
            rule_to_occurrences=rule_to_occurrences,
        )
    finally:
        if cleanup_cloned_repo and not existed_before and repo_path.exists():
            shutil.rmtree(repo_path, ignore_errors=True)


@click.command()
@click.option("--org", default="Solvro", show_default=True, help="GitHub organization")
@click.option(
    "--root-dir",
    default="~/repos",
    show_default=True,
    type=click.Path(path_type=Path),
    help="Directory for local clones",
)
@click.option(
    "--output",
    default="result.tsv",
    show_default=True,
    type=click.Path(path_type=Path),
    help="Report file path",
)
@click.option(
    "--summary-output",
    type=click.Path(path_type=Path),
    help="Markdown summary file path",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["tsv", "csv"]),
    default="tsv",
    show_default=True,
    help="Output format",
)
@click.option(
    "--cleanup-cloned-repo",
    is_flag=True,
    help="Remove repos cloned by this run after analysis",
)
def main(
    org: str,
    root_dir: Path,
    output: Path,
    summary_output: Path | None,
    output_format: str,
    cleanup_cloned_repo: bool,
) -> None:
    setup_logging()

    root_dir = root_dir.expanduser().resolve()
    output = output.expanduser().resolve()
    if summary_output is not None:
        summary_output = summary_output.expanduser().resolve()

    root_dir.mkdir(parents=True, exist_ok=True)

    repos = discover_repos(org)

    counter: Counter[str] = Counter()
    rule_to_repos: dict[str, set[str]] = defaultdict(set)
    rule_to_occurrences: dict[str, list[Occurrence]] = defaultdict(list)
    analyzed = 0
    skipped = 0

    with ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(analyze_repo, repo, root_dir, cleanup_cloned_repo)
            for repo in repos
        ]
        for future in as_completed(futures):
            result = future.result()
            if not result.ok:
                skipped += 1
                logger.error("Skipping {}: {}", result.repo.full_name, result.reason)
                continue

            analyzed += 1
            logger.info("Analyzed {}", result.repo.full_name)
            counter.update(result.counter)
            for rule, repos_set in result.rule_to_repos.items():
                rule_to_repos[rule].update(repos_set)
            for rule, occurrences in result.rule_to_occurrences.items():
                rule_to_occurrences[rule].extend(occurrences)

    export_results(output, output_format, counter, rule_to_repos)
    logger.success("Saved report to {}", output)
    if summary_output is not None:
        export_summary(
            summary_output,
            counter,
            rule_to_repos,
            rule_to_occurrences,
            analyzed,
            skipped,
        )
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
