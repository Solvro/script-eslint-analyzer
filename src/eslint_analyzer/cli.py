from __future__ import annotations

from pathlib import Path

import click

from eslint_analyzer.analysis import Analysis


@click.command(help="Analyze ESLint presets across organization repositories.")
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
    help="Markdown summary file path (eslint-disable preset)",
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
@click.option(
    "--preset",
    "preset_name",
    type=click.Choice(["eslint-disable", "eslint-errors"]),
    default="eslint-disable",
    show_default=True,
    help="Analysis preset to run",
)
def main(
    org: str,
    root_dir: Path,
    output: Path,
    summary_output: Path | None,
    output_format: str,
    cleanup_cloned_repo: bool,
    preset_name: str,
) -> None:
    Analysis.run(
        org=org,
        root_dir=root_dir,
        output=output,
        summary_output=summary_output,
        output_format=output_format,
        cleanup_cloned_repo=cleanup_cloned_repo,
        preset_name=preset_name,
    )


if __name__ == "__main__":
    main()
