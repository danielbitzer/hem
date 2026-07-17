# HEM — working conventions

## Changelog
Every PR that changes the add-on (code, blueprint, dashboard, docs shipped in
the image) must add an entry to `hem/CHANGELOG.md` under an `## Unreleased`
section at the top (create it if absent). Reference the issue/PR number where
one exists. At release time the `## Unreleased` heading is renamed to the new
version.

## Releases
The Supervisor pulls images by version tag and never re-pulls an existing
tag, so any change that should reach an installed add-on needs a version
bump. A release = bump the version in `hem/config.yaml`, `hem/pyproject.toml`
and `hem/src/hem/__init__.py`, run `uv lock` in `hem/`, rename
`## Unreleased` to the version, commit, push to `main` — CI builds and
publishes the GHCR images.

## Checks
Run `uv run ruff check .` and `uv run pytest -q` from `hem/` before
committing.
