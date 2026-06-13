#!/usr/bin/env bash
#
# Release helper for the clipwright uv workspace.
#
# Bumps every workspace package (core + members) to a single version, updates
# each member's "clipwright>=" core dependency pin to match, refreshes uv.lock,
# verifies all versions are consistent, then commits, tags vX.Y.Z and pushes.
# Pushing the tag triggers .github/workflows/publish.yml, which publishes all
# eight packages to PyPI via Trusted Publishing (no API token needed).
#
# Usage:
#   scripts/release.sh 0.1.2          # interactive: prompts before commit/push
#   scripts/release.sh 0.1.2 --yes    # non-interactive: commit/tag/push without prompt
#
# Notes:
#   - Run from a clean working tree; the release commit must contain only the
#     version bump, nothing else.
#   - Version must look like X.Y.Z, optionally with an rcN / aN / bN suffix
#     (matches the tag patterns in publish.yml).
set -euo pipefail

VERSION="${1:-}"
ASSUME_YES=0
[ "${2:-}" = "--yes" ] && ASSUME_YES=1

die() { echo "error: $*" >&2; exit 1; }

[ -n "$VERSION" ] || die "missing version argument. Usage: scripts/release.sh X.Y.Z [--yes]"

# Validate the version shape against the publish.yml tag patterns.
if ! printf '%s' "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+(rc[0-9]+|a[0-9]+|b[0-9]+)?$'; then
  die "invalid version '$VERSION'. Expected X.Y.Z optionally with rcN/aN/bN suffix."
fi

# Always operate from the repository root regardless of caller's cwd.
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

TAG="v$VERSION"

# Pre-flight checks.
[ -z "$(git status --porcelain)" ] || die "working tree is not clean. Commit or stash changes first."
git rev-parse "$TAG" >/dev/null 2>&1 && die "tag $TAG already exists."

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ]; then
  echo "warning: current branch is '$BRANCH', not 'main'." >&2
fi

# Collect every pyproject.toml: core (root) + members (clipwright-*/).
CORE_TOML="pyproject.toml"
mapfile -t MEMBER_TOMLS < <(ls clipwright-*/pyproject.toml)
[ "${#MEMBER_TOMLS[@]}" -gt 0 ] || die "found no member pyproject.toml files."
ALL_TOMLS=("$CORE_TOML" "${MEMBER_TOMLS[@]}")

# Guard against ambiguous version lines before editing.
for f in "${ALL_TOMLS[@]}"; do
  n="$(grep -cE '^version = ' "$f" || true)"
  [ "$n" -eq 1 ] || die "$f has $n top-level 'version =' lines (expected exactly 1)."
done

echo "Bumping ${#ALL_TOMLS[@]} packages to $VERSION ..."

# 1) Bump [project] version in every package.
for f in "${ALL_TOMLS[@]}"; do
  sed -i -E "s/^version = \".*\"/version = \"$VERSION\"/" "$f"
done

# 2) Pin each member's core dependency to the new version.
for f in "${MEMBER_TOMLS[@]}"; do
  sed -i -E "s/\"clipwright>=[0-9][0-9.]*(rc[0-9]+|a[0-9]+|b[0-9]+)?\"/\"clipwright>=$VERSION\"/" "$f"
done

# 3) Refresh the lockfile so it matches the bumped versions.
echo "Updating uv.lock ..."
uv lock

# 4) Verify consistency: every package version must equal VERSION, and every
#    member's core pin must equal VERSION.
echo "Verifying ..."
for f in "${ALL_TOMLS[@]}"; do
  v="$(grep -E '^version = ' "$f" | head -1 | sed -E 's/.*"(.*)".*/\1/')"
  [ "$v" = "$VERSION" ] || die "version mismatch in $f: got '$v', expected '$VERSION'."
done
for f in "${MEMBER_TOMLS[@]}"; do
  p="$(grep -oE '"clipwright>=[^"]*"' "$f" | head -1)"
  [ "$p" = "\"clipwright>=$VERSION\"" ] || die "core pin mismatch in $f: got $p, expected \"clipwright>=$VERSION\"."
done
echo "OK: all 8 packages and 7 core pins are at $VERSION."

echo
echo "Changes to be released:"
git --no-pager diff --stat

# 5) Confirm before the irreversible publish trigger.
if [ "$ASSUME_YES" -ne 1 ]; then
  printf "\nCommit, tag %s and push (this triggers PyPI publish)? [y/N] " "$TAG"
  read -r ans
  case "$ans" in
    y|Y) ;;
    *) echo "Aborted. Files are updated but not committed."; exit 0 ;;
  esac
fi

# 6) Commit, tag and push. Pushing the tag fires the publish workflow.
git add "${ALL_TOMLS[@]}" uv.lock
git commit -m "chore(release): バージョンを $VERSION に更新"
git tag "$TAG"
git push origin "$BRANCH"
git push origin "$TAG"

echo
echo "Released $TAG. Track the publish run:"
echo "  https://github.com/satoh-y-0323/clipwright/actions/workflows/publish.yml"
