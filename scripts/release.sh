#!/usr/bin/env bash
# Publish a new version of the integration.
#
# HACS installs the GitHub release, so a release is: bump the manifest version,
# commit, tag vX.Y.Z, push, and publish a GitHub release for the tag. The Release
# workflow then checks that the tag matches the manifest.
#
# Usage:
#   scripts/release.sh VERSION [--notes-file FILE] [--prerelease] [--dry-run] [--yes]
#
#   VERSION         X.Y.Z, with or without a leading "v"
#   --notes-file    Release notes (Markdown). Default: the commit subjects since
#                   the previous tag.
#   --prerelease    Mark as a pre-release: HACS offers it only to users who turn on
#                   "Show beta versions" for GeoPulse.
#   --dry-run       Run every check and show what would happen; change nothing.
#   --yes           Don't ask for confirmation.
#
# Requires git, gh (logged in) and python3. Commit your changes first: the tree
# must be clean and main must not be behind origin/main.

set -euo pipefail

MANIFEST="custom_components/geopulse_ha/manifest.json"
BRANCH="main"
REMOTE="origin"

version=""
notes_file=""
prerelease=false
dry_run=false
assume_yes=false

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --notes-file) notes_file="${2:?--notes-file needs a file}"; shift 2 ;;
    --prerelease) prerelease=true; shift ;;
    --dry-run) dry_run=true; shift ;;
    --yes|-y) assume_yes=true; shift ;;
    -h|--help) usage ;;
    -*) echo "Unknown option: $1" >&2; usage 2 ;;
    *)
      [[ -z "$version" ]] || { echo "Only one version, please" >&2; usage 2; }
      version="${1#v}"; shift ;;
  esac
done
[[ -n "$version" ]] || usage 2

cd "$(git rev-parse --show-toplevel)"
tag="v$version"
problems=0

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
fail() {
  # In a dry run, collect every problem instead of stopping at the first.
  printf '\033[31mERROR: %s\033[0m\n' "$*" >&2
  if $dry_run; then problems=$((problems + 1)); else exit 1; fi
}

for tool in git gh python3; do
  command -v "$tool" >/dev/null || { echo "ERROR: $tool is not installed" >&2; exit 1; }
done

# ------------------------------------------------------------------ checks

say "Checking version $tag"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "Version must be X.Y.Z, got '$version'"
current=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['version'])" "$MANIFEST")
python3 - "$current" "$version" <<'EOF' || fail "$tag is not newer than the manifest version $current"
import sys
old, new = (tuple(int(p) for p in v.split(".")) for v in sys.argv[1:])
sys.exit(0 if new > old else 1)
EOF

say "Checking git state"
git fetch --quiet --tags "$REMOTE" "$BRANCH"
[[ "$(git branch --show-current)" == "$BRANCH" ]] || fail "Not on $BRANCH"
[[ -z "$(git status --porcelain)" ]] || fail "Working tree has uncommitted changes (commit or stash them)"
if ! git merge-base --is-ancestor "$REMOTE/$BRANCH" HEAD; then
  fail "$BRANCH is behind or has diverged from $REMOTE/$BRANCH (pull first)"
fi
if git rev-parse -q --verify "refs/tags/$tag" >/dev/null \
  || [[ -n "$(git ls-remote --tags "$REMOTE" "refs/tags/$tag")" ]]; then
  fail "Tag $tag already exists"
fi
gh auth status >/dev/null 2>&1 || fail "gh is not logged in (run: gh auth login)"

say "Running ruff and pytest"
ruff=".venv/bin/ruff"; [[ -x "$ruff" ]] || ruff="ruff"
pytest=".venv/bin/pytest"; [[ -x "$pytest" ]] || pytest="pytest"
"$ruff" check . || fail "ruff check failed"
"$ruff" format --check . || fail "ruff format --check failed"
"$pytest" -q || fail "Tests failed"

# ------------------------------------------------------------------ notes

previous=$(git describe --tags --abbrev=0 --match 'v*' 2>/dev/null || true)
notes_tmp=$(mktemp)
trap 'rm -f "$notes_tmp"' EXIT
if [[ -n "$notes_file" ]]; then
  [[ -f "$notes_file" ]] || fail "Notes file $notes_file not found"
  [[ -f "$notes_file" ]] && cp "$notes_file" "$notes_tmp"
else
  range="${previous:+$previous..}HEAD"
  {
    echo "## Changes"
    echo
    git log --no-merges --format='- %s' "$range"
  } >"$notes_tmp"
fi

echo
say "Release summary"
echo "  Version:     $current -> $version"
echo "  Tag:         $tag on $(git rev-parse --short HEAD) ($BRANCH)"
echo "  Previous:    ${previous:-none}"
echo "  Pre-release: $prerelease"
echo "  Notes:"
sed 's/^/    | /' "$notes_tmp"
echo

if $dry_run; then
  if [[ $problems -gt 0 ]]; then
    say "Dry run: $problems problem(s) to fix before releasing"
    exit 1
  fi
  say "Dry run: all checks passed. Would run:"
  echo "  set version $version in $MANIFEST"
  echo "  git commit -m 'Release $tag' $MANIFEST"
  echo "  git tag -a $tag -m 'Release $tag'"
  echo "  git push $REMOTE $BRANCH $tag"
  echo "  gh release create $tag --title $tag --notes-file <notes>$($prerelease && echo ' --prerelease')"
  exit 0
fi

if ! $assume_yes; then
  read -r -p "Publish $tag? [y/N] " answer
  [[ "$answer" =~ ^[Yy]$ ]] || { echo "Aborted; nothing changed."; exit 1; }
fi

# ------------------------------------------------------------------ release

say "Bumping $MANIFEST to $version"
# Replace only the version so the rest of the file keeps its formatting.
python3 - "$MANIFEST" "$version" <<'EOF'
import re, sys
path, version = sys.argv[1:]
text = open(path).read()
new, count = re.subn(r'("version"\s*:\s*")[^"]*(")', rf"\g<1>{version}\g<2>", text)
if count != 1:
    sys.exit(f"Expected one version field in {path}, found {count}")
open(path, "w").write(new)
EOF

say "Committing and tagging"
git commit --quiet -m "Release $tag" -- "$MANIFEST"
git tag -a "$tag" -m "Release $tag"

recover() {
  cat >&2 <<EOF

The release stopped after the local commit and tag. To undo them:
  git tag -d $tag && git reset --hard HEAD~1
Or fix the problem and finish by hand:
  git push $REMOTE $BRANCH $tag
  gh release create $tag --title $tag --notes-file <notes>
EOF
}
trap 'recover; rm -f "$notes_tmp"' ERR

say "Pushing $BRANCH and $tag"
git push --quiet "$REMOTE" "$BRANCH" "$tag"

say "Creating the GitHub release"
release_args=(--title "$tag" --notes-file "$notes_tmp" --verify-tag)
$prerelease && release_args+=(--prerelease)
url=$(gh release create "$tag" "${release_args[@]}")
trap - ERR

say "Released $tag: $url"
echo "HACS shows the update under Settings → Updates (it checks GitHub periodically;"
echo "HACS → GeoPulse → ⋮ → Update information forces a check)."
