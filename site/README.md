# ariadne site

This is the public documentation site for `ariadne`, built as a small Next app.
It is deployed to Vercel at `ariadne.hyperplex.org`.

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

## Deploying to Vercel

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
