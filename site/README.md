# tweet-threader site

This is the public site for `tweet-threader`, built as a small Next app for
Vercel.

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

## Deploying To Vercel

The intended production hostname is `ariadne.hyperplex.org`.

```bash
vercel link --project tweet-threader
vercel --prod
vercel alias set <deployment-url> ariadne.hyperplex.org
```

## Useful Commands

```bash
npm run lint
npm run build
npm run start
```
