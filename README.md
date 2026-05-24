# Solvro ESLint Disable Analyzer

CLI tool that discovers selected Solvro repositories, syncs local clones, scans for ESLint disable directives, and exports aggregated ignore statistics. A scheduled GitHub Actions workflow runs this analyzer every Sunday at noon UTC, regenerates the findings below, and commits the README update back to this repository.

## Current Findings

<!-- eslint-analyzer-summary:start -->
Analyzed repositories: **49**
Skipped repositories: **0**
Total ESLint disable directives found: **381**
Unique ignored rules: **85**

### Top 10 Ignored Rules

| Rule | Count | Repositories |
| --- | ---: | --- |
| `@typescript-eslint/no-unsafe-assignment` | 25 | Solvro/backend-eventownik-v2, Solvro/backend-eventownik-v3, Solvro/backend-jak-doczlapie, Solvro/backend-topwr, Solvro/web-strona-w4, Solvro/web-testownik, Solvro/web-topwr, Solvro/web-unite-x-graz |
| `@typescript-eslint/no-unsafe-return` | 23 | Solvro/backend-eventownik-v3, Solvro/web-eventownik-v2, Solvro/web-strona-w4, Solvro/web-topwr, Solvro/web-unite-x-graz |
| `@typescript-eslint/no-unsafe-call` | 21 | Solvro/backend-eventownik-v3, Solvro/backend-jak-doczlapie, Solvro/web-eventownik-v2, Solvro/web-testownik, Solvro/web-topwr |
| `@typescript-eslint/no-unnecessary-condition` | 19 | Solvro/backend-eventownik-v2, Solvro/backend-jak-doczlapie, Solvro/backend-topwr, Solvro/web-eventownik-v2, Solvro/web-planer, Solvro/web-strona-w4, Solvro/web-testownik |
| `@typescript-eslint/no-deprecated` | 19 | Solvro/web-testownik |
| `react-hooks/exhaustive-deps` | 17 | Solvro/web-eventownik-v2, Solvro/web-planer, Solvro/web-testownik |
| `import/no-default-export` | 13 | Solvro/backend-eventownik-v3, Solvro/web-eventownik-v2, Solvro/web-juwenalia, Solvro/web-planer, Solvro/web-promochator, Solvro/web-testownik, Solvro/web-unite-x-graz |
| `@typescript-eslint/unbound-method` | 13 | Solvro/backend-eventownik-v3 |
| `no-useless-constructor` | 12 | Solvro/backend-eventownik-v2 |
| `@next/next/no-img-element` | 11 | Solvro/web-planer, Solvro/web-testownik, Solvro/web-topwr |
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

## Output schema

Columns:

1. `rule`
2. `count`
3. `repositories` (comma-separated sorted `Org/repo` list)
