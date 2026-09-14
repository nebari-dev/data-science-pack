# Data Science Pack docs (Astro + Starlight)

Astro-powered documentation site for `data-science-pack`, built on Starlight
with the shared `@nebari/starlight` theme. Mirrors the pattern used by
`nebari-dev/provenance-collector-pack`, `nebari-dev/llm-serving-pack`, and
`nebari-dev/nebari-frames`: build on Astro, deploy to Cloudflare Pages, and
route through the `packs.nebari.dev` portal.

Deployed at **`https://packs.nebari.dev/data-science-pack/`** (the portal
Worker proxies that path to the `data-science-pack` Cloudflare Pages
project). Pull requests get an ephemeral preview at
`https://<branch>.data-science-pack.pages.dev`.

## Local development

```sh
cd docs
npm install
npm run dev     # http://localhost:4321
```

Then edit files under `src/content/docs/`. Astro reloads on save.

## Layout

Relative to this directory:

- `astro.config.mjs` - Starlight + `@nebari/starlight` + `rehype-mermaid` + `remark-base-links`
- `package.json`, `tsconfig.json`, `.gitignore`
- `src/content.config.ts` - Starlight docs collection
- `src/content/docs/*.md` - actual pages (frontmatter required: `title`, `description`)
- `src/plugins/remark-base-links.ts` - rewrites root-absolute links when deployed under a subpath
- `tests/remark-base-links.test.ts` - vitest for the plugin

Related, at the repo root:

- `.github/workflows/docs.yml` - build, link-check, deploy to Cloudflare Pages
- `.github/workflows/docs-preview-cleanup.yml` - delete a PR's previews when it closes
- `scripts/check-links.sh` - internal-link validator against `docs/dist/`

## Deploy pipeline

`docs.yml` runs on push-to-main and on PRs touching `docs/**`,
`scripts/check-links.sh`, or the workflow itself.

1. Node 22, `npm ci` in `docs/`.
2. Installs Playwright (for `rehype-mermaid`).
3. Runs `vitest` (the `remark-base-links` plugin test).
4. Computes `SITE`/`BASE` per event: on `main`, `BASE=/data-science-pack/`
   (served under the portal subpath); on a PR, `BASE=/` (the preview lives at
   its own `*.pages.dev` subdomain).
5. Builds, then runs `scripts/check-links.sh` against `docs/dist/` - every
   internal `href`/`src` must resolve to a file.
6. Deploys `docs/dist/` to the `data-science-pack` Cloudflare Pages project
   with `wrangler pages deploy`. Main deploys to production; PRs deploy a
   preview and get the URL posted as a sticky comment.

## Prerequisites (one-time)

The deploy needs two secrets, available to the workflow as
`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` (org-level secrets shared
across the pack repos, or repo-level). The Cloudflare Pages **project is
self-provisioning** - the deploy step runs `wrangler pages project create` if
it does not exist, so there is no manual dashboard step.

Portal routing at `packs.nebari.dev/data-science-pack/` is driven by
`docs_site: true` in the repo-root `pack-metadata.yaml`: the
`nebari-dev/software-pack-dashboard` route generator picks it up and maps the
slug to `https://data-science-pack.pages.dev`.

If the secrets are not yet visible to this repo, PR builds still run and gate
(deploy skips); the first push-to-main deploy fails loudly until they resolve.

## Content

Wired into the sidebar:

- `index.md` - landing page for the docs site.
- Guides: `quick-start.md`, `architecture.md`, `shared-storage.md`.
- Reference: `configuration.md`, `nebariapp-integration.md`.

## Not yet added

- Custom favicon / brand asset overrides beyond what `@nebari/starlight` provides.
