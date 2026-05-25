from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import click
from loguru import logger


@dataclass(frozen=True)
class Repo:
    org: str
    name: str
    default_branch: str

    @property
    def full_name(self) -> str:
        return f"{self.org}/{self.name}"


class BasePreset(ABC):
    def __init__(self, repo: Repo, root_dir: Path):
        self.repo = repo
        self.root_dir = root_dir
        self.repo_path = root_dir / repo.name
        self.package_manager: str | None = None

    @staticmethod
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

    def prepare_repo(self) -> tuple[bool, str | None]:
        return self.sync_repo()

    def detect_package_manager(self) -> str:
        if (self.repo_path / "pnpm-lock.yaml").exists():
            self.package_manager = "pnpm"
            return self.package_manager
        if (self.repo_path / "package-lock.json").exists():
            self.package_manager = "npm"
            return self.package_manager
        if (self.repo_path / "yarn.lock").exists():
            self.package_manager = "yarn"
            return self.package_manager
        self.package_manager = "npm"
        return self.package_manager

    def install_dependencies(self) -> None:
        package_manager = self.detect_package_manager()
        if package_manager == "pnpm":
            self.run_cmd(["pnpm", "install", "--frozen-lockfile"], cwd=self.repo_path)
            return
        if package_manager == "npm":
            self.run_cmd(["npm", "ci"], cwd=self.repo_path)
            return
        if package_manager == "yarn":
            self.run_cmd(["yarn", "install", "--immutable"], cwd=self.repo_path)
            return
        raise click.ClickException(f"Unsupported package manager: {package_manager}")

    @abstractmethod
    def analyze(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def generate_markdown(self, result: dict) -> str:
        raise NotImplementedError

    def is_git_repo(self, path: Path) -> bool:
        try:
            proc = self.run_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=path)
            return proc.stdout.strip() == "true"
        except subprocess.CalledProcessError:
            return False

    def repo_dirty(self, path: Path) -> bool:
        proc = self.run_cmd(["git", "status", "--porcelain"], cwd=path)
        return bool(proc.stdout.strip())

    def has_local_branch(self, path: Path, branch: str) -> bool:
        proc = self.run_cmd(["git", "branch", "--list", branch], cwd=path)
        return bool(proc.stdout.strip())

    def sync_repo(self) -> tuple[bool, str | None]:
        repo_path = self.repo_path
        if not repo_path.exists():
            logger.info("Cloning {}", self.repo.full_name)
            try:
                self.run_cmd(
                    ["gh", "repo", "clone", self.repo.full_name, str(repo_path)]
                )
                return True, None
            except subprocess.CalledProcessError as exc:
                return False, f"clone failed: {exc.stderr.strip()}"

        if not self.is_git_repo(repo_path):
            return False, "path exists but is not a git repository"

        try:
            self.run_cmd(["git", "fetch", "origin"], cwd=repo_path)
        except subprocess.CalledProcessError as exc:
            return False, f"fetch failed: {exc.stderr.strip()}"

        local_ref = f"refs/heads/{self.repo.default_branch}"
        remote_ref = f"refs/remotes/origin/{self.repo.default_branch}"
        local_sha = self.run_cmd(
            ["git", "rev-parse", local_ref], cwd=repo_path, check=False
        ).stdout.strip()
        remote_sha = self.run_cmd(
            ["git", "rev-parse", remote_ref], cwd=repo_path, check=False
        ).stdout.strip()

        if not remote_sha:
            return False, f"missing remote branch origin/{self.repo.default_branch}"
        if local_sha == remote_sha and local_sha:
            return True, None
        if self.repo_dirty(repo_path):
            return False, "repo is dirty and behind remote"

        try:
            if self.has_local_branch(repo_path, self.repo.default_branch):
                self.run_cmd(
                    ["git", "checkout", self.repo.default_branch], cwd=repo_path
                )
            else:
                self.run_cmd(
                    [
                        "git",
                        "checkout",
                        "-b",
                        self.repo.default_branch,
                        "--track",
                        f"origin/{self.repo.default_branch}",
                    ],
                    cwd=repo_path,
                )
            self.run_cmd(
                ["git", "pull", "--ff-only", "origin", self.repo.default_branch],
                cwd=repo_path,
            )
            return True, None
        except subprocess.CalledProcessError as exc:
            return False, f"sync failed: {exc.stderr.strip()}"
