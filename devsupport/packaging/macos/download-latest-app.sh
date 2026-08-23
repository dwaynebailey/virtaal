#!/bin/bash
# Downloads the most recent build-macos-app CI artifact for the given (or
# current) branch and extracts it to dist/downloaded/Virtaal.app, ready to
# test locally without waiting for a build here. Needs `gh` authenticated
# against this repo (same as everything else in this project's CI
# tooling).
#
# Usage: devsupport/packaging/macos/download-latest-app.sh [branch]
#
# The CI run's overall status is deliberately NOT used to find the latest
# build: test-windows is a known, separately-tracked failure (see
# ISSUE_TRIAGE.md) that keeps the *whole run* red even on commits where
# build-macos-app succeeded cleanly and uploaded its artifact. Querying
# the artifacts API directly instead - a Virtaal-macos-app artifact
# existing at all is proof that job succeeded, since upload only happens
# after it does.
set -eu
cd "$(git rev-parse --show-toplevel)"

REPO="dwaynebailey/virtaal"
export BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"
DEST="dist/downloaded"

echo "Looking for the latest Virtaal-macos-app artifact on branch '$BRANCH'..."

match=$(gh api "repos/$REPO/actions/artifacts?per_page=100" \
  --jq '[.artifacts[] | select(.name == "Virtaal-macos-app" and .expired == false and .workflow_run.head_branch == $ENV.BRANCH)] | sort_by(.created_at) | last | if . == null then empty else [.workflow_run.id, .workflow_run.head_sha[0:8], .created_at] | @tsv end')

if [ -z "$match" ]; then
  echo "No Virtaal-macos-app artifact found for branch '$BRANCH'." >&2
  echo "Has the build-macos-app CI job run (and succeeded) for this branch yet?" >&2
  exit 1
fi

run_id=$(echo "$match" | cut -f1)
sha=$(echo "$match" | cut -f2)
created=$(echo "$match" | cut -f3)
echo "Found artifact from commit $sha, built $created"

rm -rf "$DEST"
mkdir -p "$DEST"
gh run download "$run_id" --repo "$REPO" --name Virtaal-macos-app --dir "$DEST"

# The artifact is a ditto-created zip (see ci.yml's "Package the bundle
# for upload" step) - ditto is required to extract it correctly too, to
# preserve the embedded code signature and the Contents/MacOS/share
# symlink the way a plain unzip may not (confirmed this matters - see
# ISSUE_TRIAGE.md's git-SHA-visibility-adjacent notes on the artifact
# packaging fix).
ditto -x -k "$DEST/Virtaal-macos-app.zip" "$DEST"
rm "$DEST/Virtaal-macos-app.zip"

echo "Extracted to $DEST/Virtaal.app"
echo
echo "This build is only ad-hoc signed, not notarized (see ISSUE_TRIAGE.md's"
echo "#3313 entry) - macOS will still block a first launch downloaded this"
echo "way. Clear the quarantine flag and re-sign locally before opening:"
echo "  xattr -cr $DEST/Virtaal.app"
echo "  codesign --force --deep -s - $DEST/Virtaal.app"
echo
echo "Then launch it - Virtaal.app is a bundle *directory*, not a plain"
echo "executable, so it can't be run directly (zsh: permission denied):"
echo "  open $DEST/Virtaal.app                              # normal launch"
echo "  $DEST/Virtaal.app/Contents/MacOS/virtaal --version   # or run the binary inside it directly"
