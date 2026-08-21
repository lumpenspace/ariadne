# Releasing

The PyPI distribution is `ariadne-x`; the Python import package and CLI
command remain `ariadne`. The package version is sourced from `__version__` in
`src/ariadne/__init__.py`, which Hatch reads through `pyproject.toml`.

## GitHub Release → PyPI Trusted Publishing

The one-time pending trusted publisher on PyPI uses:

- Project: `ariadne-x`
- Owner/repository: `lumpenspace/ariadne`
- Workflow: `publish.yml`
- Environment: `pypi`

For each release:

1. Bump `__version__` in `src/ariadne/__init__.py` and run `uv lock`.
2. Merge the release commit to `main` and wait for CI.
3. Publish a GitHub Release with a matching `vX.Y.Z` tag.

`.github/workflows/publish.yml` runs lint and tests, builds the sdist and wheel,
then uploads them to PyPI through OIDC. No PyPI password or API token is stored
in GitHub.
