#!/usr/bin/env bash
# Re-install namkha-calculator into this venv as an editable install pointing
# at ../namkha-calculator, so library edits take effect without a reinstall.
#
# pyproject.toml keeps the PyPI dependency on purpose: the Dockerfile copies
# only pyproject.toml + poetry.lock into a build context that has no
# ../namkha-calculator, so a `develop = true` path dep would break the fly.io
# build. The editable link lives in the local venv only – which means every
# `poetry install` / `sync` / `add` reinstalls the pinned wheel over it and
# silently drops the link. Re-run this after any of them.
#
# --no-deps leaves skyfield and timezonefinder under poetry's lock.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
calculator_repo="$repo_root/../namkha-calculator"

if [[ ! -f "$calculator_repo/pyproject.toml" ]]; then
    echo "namkha-calculator not found at $calculator_repo" >&2
    exit 1
fi

cd "$repo_root"
poetry run pip install -e "$calculator_repo" --no-deps

# Confirm the link took: this must print a path under ../namkha-calculator/src,
# not one inside site-packages.
poetry run python -c 'import namkha_calculator; print("editable:", namkha_calculator.__file__)'
