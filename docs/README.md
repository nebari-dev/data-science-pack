# Nebari Data Science Pack Documentation

This directory contains the [Docusaurus](https://docusaurus.io/) site for the Nebari Data Science pack. The site is written in TypeScript.

## Prerequisites

- Node.js `>= 20` (enforced by the `engines` field in `package.json`).
- npm (bundled with Node.js).

## Install

```bash
cd docs
npm install
```

## Local development

```bash
npm start
```

Starts the Docusaurus dev server with hot reload on http://localhost:3000/.

## Production build

```bash
npm run build
```

Emits static files to `docs/build/`. The search index is generated as part of the production build.

## Preview the production build

```bash
npm run serve
```

Serves the contents of `docs/build/` locally so you can verify the production output, including search.

## Type checking

```bash
npm run typecheck
```

## CI

The [`Docs` workflow](../.github/workflows/docs.yml) builds the site for every pull request and push to `main` that touches `docs/`.
