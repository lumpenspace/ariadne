# ariadne site

This is the public documentation site for `ariadne`, built as a small Next app.
It keeps a standard Next build for Vercel and a vinext build for OpenAI Sites.

## Prerequisites

- Node.js `>=22.13.0`

## Local Development

```bash
npm install
npm run dev
```

## Verification

```bash
npm test
```

`npm test` runs the linter and a production Next build.

To verify the Sites/Cloudflare worker bundle as well:

```bash
npm run build:sites
```

## OpenAI Sites

The local `.openai/hosting.json` binds this source tree to its Sites project.
`npm run build:sites` writes the deployable worker and static assets to
`dist/`.

## Deploying To Vercel

The intended production hostname is `ariadne.hyperplex.org`.

```bash
vercel link --project ariadne
vercel --prod
vercel alias set <deployment-url> ariadne.hyperplex.org
```

## Useful Commands

```bash
npm run lint
npm run build
npm run start
```
