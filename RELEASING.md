# Releasing

Releases are published to PyPI from GitHub Actions with Trusted Publishing, so
the repository does not need to contain a PyPI password or API token.

## One-time setup

1. Create the public `LukasBuschmann/slurm-avail` GitHub repository.
2. Create a `pypi` environment in the repository settings. Requiring approval
   for that environment is recommended.
3. On PyPI, add a pending Trusted Publisher for project `slurm-avail` with:
   - Owner: `LukasBuschmann`
   - Repository: `slurm-avail`
   - Workflow: `release.yml`
   - Environment: `pypi`

## Publish a version

1. Update the version in `pyproject.toml`.
2. Run `ruff format --check .`, `ruff check .`, `pytest`, and `python -m build`.
3. Commit the version change and create a GitHub release tagged `vX.Y.Z`.

Publishing the GitHub release starts `.github/workflows/release.yml`. It builds
both the source distribution and wheel, then publishes them to PyPI after the
`pypi` environment has approved the job.
