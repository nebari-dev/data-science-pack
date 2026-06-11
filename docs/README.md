# Nebari Data Science Pack Documentation

This directory contains the [Docusaurus 3.5.2](https://docusaurus.io/) site for the Nebari Data Science pack.

## Prerequisites

- Node.js `>= 18` (enforced by the `engines` field in `package.json`).
- Yarn (Classic, v1.22.x). Install globally with `npm install -g yarn`, then verify with `yarn --version`.

The site has been built and tested against Node 20 and Yarn 1.22.22.

## Install

```bash
cd docs
yarn install
```

## Local development

```bash
yarn start
```

Starts the Docusaurus dev server with hot reload on http://localhost:3000/.

Note: the lunr search index is generated only by `yarn build`. The search box in the dev server will return no results; use a production build to exercise search.

## Production build

```bash
yarn build
```

Emits static files to `docs/build/`. The build step also produces the lunr search index via `docusaurus-lunr-search`.

## Preview the production build

```bash
yarn run serve
```

Serves the contents of `docs/build/` locally so you can verify the production output, including search.

## Deployment

The site deploys automatically via GitHub Pages whenever changes land on the `main` branch. The GitHub Actions workflow builds the site from the `docs/` directory and publishes it to the `gh-pages` branch.
