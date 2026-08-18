# slurm-avail

`slurm-avail` is a keyboard-driven terminal dashboard for monitoring one or
more Slurm clusters. It can query Slurm locally or through SSH and presents
node, CPU, memory, GPU, filesystem, login-node, reservation, and running-job
availability in one place.

The dashboard is configured locally. Nothing needs to be installed on the
clusters.

## Installation

The recommended installation uses [`uv`](https://docs.astral.sh/uv/):

```console
uv tool install slurm-avail
slurm-avail
```

Alternatively, use `pipx`:

```console
pipx install slurm-avail
slurm-avail
```

Before the first PyPI release, install directly from GitHub:

```console
uv tool install git+https://github.com/LukasBuschmann/slurm-avail.git
```

The package has no third-party runtime dependencies. It requires Python 3.11
or newer and a POSIX terminal with curses support.

## First run

Run:

```console
slurm-avail
```

On the first run, `slurm-avail` creates
`~/.config/slurm-avail/config.toml` and opens the Config view. Select the
initial local cluster and press `Enter` to edit it, or press `a` to add an SSH
cluster. Press `s` to save and apply the configuration, then use `Tab` to move
between views.

You can return to configuration directly with:

```console
slurm-avail --init
```

Use another configuration file with:

```console
slurm-avail --config /path/to/config.toml
```

The `XDG_CONFIG_HOME` environment variable is respected. See the
[complete example configuration](https://github.com/LukasBuschmann/slurm-avail/blob/main/config.example.toml).

## Cluster modes

A local cluster runs Slurm commands on the machine where the dashboard is
started:

```toml
[[clusters]]
name = "LOCAL"
mode = "local"
focus = "auto"
filesystems = ["/home"]
```

An SSH cluster can have multiple login nodes. They are attempted in order and
the next endpoint is used automatically when one fails:

```toml
[[clusters]]
name = "GPU CLUSTER"
mode = "ssh"
addresses = ["login1.example.org", "login2.example.org"]
user = "cluster-user"
focus = "gpu"
filesystems = ["/home", "/scratch"]
slurm_bin_path = "/opt/slurm/bin"
```

The `user` and `slurm_bin_path` fields are optional. Without `user`, the
OpenSSH configuration is used. Without `slurm_bin_path`, Slurm is expected on
the remote `PATH`.

## Requirements

On the computer running `slurm-avail`:

- Python 3.11 or newer
- a curses-capable POSIX terminal
- OpenSSH's `ssh` command when using SSH clusters

On each configured cluster endpoint:

- `/bin/sh`, `id`, and `date`
- Slurm's `scontrol` and `squeue`
- `df` and `tail` when filesystem monitoring is enabled (`timeout` is used
  when available)

SSH authentication should already work non-interactively, normally through an
SSH agent or keys and `~/.ssh/config`. Passwords and private keys are never
stored in the dashboard configuration.

## Useful commands

```console
slurm-avail --help
slurm-avail --version
slurm-avail --once
slurm-avail --view forecast --cluster 2
uv tool upgrade slurm-avail
uv tool uninstall slurm-avail
```

## Development

```console
git clone https://github.com/LukasBuschmann/slurm-avail.git
cd slurm-avail
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
ruff check .
```

## License

MIT
