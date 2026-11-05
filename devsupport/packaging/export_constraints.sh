#!/bin/bash
# Writes uv.lock's versions of the release builds' Python packages to $1,
# as pip constraints for PIP_CONSTRAINT.
set -eu
uv export --quiet --locked --no-hashes --no-emit-project --extra spellcheck --group build-app -o "$1"
