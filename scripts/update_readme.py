from __future__ import annotations

import argparse
from pathlib import Path

START_MARKER = "<!-- eslint-analyzer-summary:start -->"
END_MARKER = "<!-- eslint-analyzer-summary:end -->"


def replace_section(readme_path: Path, summary_path: Path) -> None:
    readme = readme_path.read_text(encoding="utf-8")
    summary = summary_path.read_text(encoding="utf-8").strip()

    start = readme.find(START_MARKER)
    end = readme.find(END_MARKER)
    if start == -1 or end == -1 or start >= end:
        raise SystemExit(
            f"Could not find {START_MARKER} / {END_MARKER} section in {readme_path}"
        )

    replacement = f"{START_MARKER}\n{summary}\n{END_MARKER}"
    updated = readme[:start] + replacement + readme[end + len(END_MARKER) :]
    readme_path.write_text(updated, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replace the generated ESLint analyzer README section."
    )
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--summary", type=Path, default=Path("summary.md"))
    args = parser.parse_args()

    replace_section(args.readme, args.summary)


if __name__ == "__main__":
    main()
