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
the source distribution, wheel, and standalone Linux x86-64 executable. The
workflow publishes the Python distributions to PyPI after the `pypi`
environment has approved the job. It attaches the executable and its SHA-256
checksum to the GitHub release.

## Rebuild Linux release assets

The Linux executable can be rebuilt and attached without publishing to PyPI
again. Open **Actions > Publish release > Run workflow** and enter the existing
release tag, or run:

```console
gh workflow run release.yml -f release_tag=vX.Y.Z
```

Manual runs build the executable from the current default branch and replace
the two Linux assets on the selected release.
