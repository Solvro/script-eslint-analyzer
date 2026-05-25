# Solvro ESLint Disable Analyzer

CLI tool that discovers selected Solvro repositories, syncs local clones, scans for ESLint disable directives, and exports aggregated ignore statistics. A scheduled GitHub Actions workflow runs this analyzer every Sunday at noon UTC, regenerates the findings below, and commits the README update back to this repository.

## Current Findings

<!-- eslint-analyzer-summary:start -->
| Metric | Value |
| --- | ---: |
| Last updated | **2026-05-25 15:35 UTC** |
| Analyzed repositories | **49** |
| Skipped repositories | **None** |
| Total ESLint disable directives found | **381** |
| Unique ignored rules | **85** |
<!-- eslint-analyzer-summary:end -->

## Prerequisites

- Python 3.14+
- [`uv`](https://docs.astral.sh/uv/)
- Git
- GitHub CLI `gh` authenticated (`gh auth login`)

## Install deps

```bash
uv sync
```

## Lint and format

```bash
./scripts/lint.sh
./scripts/format.sh
```

## Run

```bash
uv run eslint-analyzer --org Solvro --root-dir ~/repos --output result.tsv --format tsv --summary-output summary.md
```

## Options

- `--org` GitHub organization (default: `Solvro`)
- `--root-dir` local clone root directory (default: `~/repos`)
- `--output` output report path (default: `result.tsv`)
- `--summary-output` optional Markdown summary file path, for example `summary.md`
- `--format` report format (`tsv` or `csv`, default: `tsv`)
- `--cleanup-cloned-repo` delete repositories cloned during current run after analysis
- `--preset` analysis preset (`eslint-disable` or `eslint-errors`, default: `eslint-disable`)
- `--jobs` maximum number of repositories analyzed in parallel (default: `4`)

## Output schema

### `eslint-disable` preset

1. `rule`
2. `count`
3. `repositories` (comma-separated sorted `Org/repo` list)

### `eslint-errors` preset

Primary report (`--output`) columns:

1. `rule`
2. `errors`
3. `warnings`

Per-repository report (`<output>.per_repo.<ext>`) columns:

1. `repo`
2. `rule`
3. `errors`
4. `warnings`
