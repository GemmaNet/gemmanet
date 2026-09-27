#!/bin/sh
# Regenerate the hash-locked dependency files (run with Python 3.11, the
# version of the images and CI):
#   pip install pip-tools && scripts/lock.sh
# Images and CI install only from these files, with pip --require-hashes.
set -e
cd "$(dirname "$0")/.."
compile() { pip-compile --quiet --generate-hashes --strip-extras --no-emit-index-url "$@"; }
# coordinator image: the package's own dependencies plus [server]
compile --extra server --output-file requirements/app.txt pyproject.toml
# CI and development: everything, plus the build backend for `pip install -e .`
compile --extra server --extra node --extra dev --all-build-deps --allow-unsafe \
    --output-file requirements/dev.txt pyproject.toml
# docs (Docker docs stage and the Cloudflare Pages build)
compile --output-file docs/requirements.txt docs/requirements.in
