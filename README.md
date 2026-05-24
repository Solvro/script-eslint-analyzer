# Solvro ESLint Disable Analyzer

CLI tool that discovers selected Solvro repositories, syncs local clones, scans for ESLint disable directives, and exports aggregated ignore statistics.

## Prerequisites

- Python 3.14+
- [`uv`](https://docs.astral.sh/uv/)
- Git
- GitHub CLI `gh` authenticated (`gh auth login`)

## Install deps

```bash
uv sync
```

## Run

```bash
uv run eslint-analyzer --org Solvro --root-dir ~/repos --output result.tsv --format tsv
```

## Options

- `--org` GitHub organization (default: `Solvro`)
- `--root-dir` local clone root directory (default: `~/repos`)
- `--output` output report path (default: `result.tsv`)
- `--format` report format (`tsv` or `csv`, default: `tsv`)

## Output schema

Columns:

1. `rule`
2. `count`
3. `repositories` (comma-separated sorted `Org/repo` list)
