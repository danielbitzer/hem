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
committing. For frontend changes also run `bun run typecheck` and
`bun run build` from `hem/frontend/`.

## Frontend (hem/frontend)
React 19 (+ React Compiler) + TypeScript + Recharts + Tailwind v4, built by
Vite with Bun as package manager (`bunfig.toml` pins `minimumReleaseAge` to
7 days — keep it). `bun run build` outputs to `hem/src/hem/web/dist`
(gitignored); CI builds it once and the per-arch image builds COPY it — never
add a Node/Bun build step to the Dockerfile (aarch64 builds run under QEMU).
Dev: `bun run dev` proxies `/api` + `/health` to a running HEM on :8099. The
page is served behind HA ingress: every URL must stay relative (`base: './'`,
fetch `./api/...`) and the bundle fully offline (no CDN).

UI components are shadcn (`src/components/ui`, checked in — edit freely, or
re-generate with `bunx shadcn add <name>`); forms use TanStack Form, data
fetching TanStack Query, API contracts are Zod schemas in `src/api.ts`.
Dark mode follows `prefers-color-scheme` via a custom variant in `index.css`
— HA ingress never sets a `.dark` class, so don't use class-based theming.
