#!/bin/sh
# make_release.sh <version> — build a GitHub release of nfupgrader.
#
# Produces, in dist/ (or $NFU_RELEASE_OUT):
#   nfupgrader-<version>.tar.gz   the tool, from git tag v<version> (no tests)
#   install.sh                    tools/install.sh with the version, the
#                                 tarball's SHA-256 and the GitHub repo filled in
# then prints the `gh release create` command to publish both.
#
# The tag must exist and the working tree must be clean, so a release is always
# exactly a tagged commit. Test overrides: NFU_RELEASE_REF (build from this ref,
# skipping the tag/clean checks), NFU_RELEASE_REPO (owner/name instead of the
# origin remote), NFU_RELEASE_OUT.
set -eu

VER="${1:-}"
[ -n "$VER" ] || { echo "usage: $0 <version>   e.g. $0 1.0.0" >&2; exit 2; }
case "$VER" in
	*[!0-9A-Za-z.-]*) echo "bad version: $VER" >&2; exit 2 ;;
esac

TOP=$(cd "$(dirname "$0")/.." && pwd)
cd "$TOP"
OUT="${NFU_RELEASE_OUT:-$TOP/dist}"

if [ -n "${NFU_RELEASE_REF:-}" ]; then
	REF="$NFU_RELEASE_REF"
else
	REF="v$VER"
	git rev-parse -q --verify "refs/tags/$REF" >/dev/null ||
		{ echo "tag $REF does not exist (git tag -a $REF -m ...)" >&2; exit 1; }
	[ -z "$(git status --porcelain)" ] ||
		{ echo "working tree is not clean; commit or stash first" >&2; exit 1; }
fi

if [ -n "${NFU_RELEASE_REPO:-}" ]; then
	REPO="$NFU_RELEASE_REPO"
else
	# owner/name from git@github.com:owner/name.git or https://github.com/owner/name(.git)
	REPO=$(git remote get-url origin |
		sed -n 's#^\(git@github\.com:\|https://github\.com/\)\([^/]*/[^/]*\)$#\2#p' |
		sed 's/\.git$//')
	[ -n "$REPO" ] || { echo "origin is not a GitHub repo; set NFU_RELEASE_REPO" >&2; exit 1; }
fi

mkdir -p "$OUT"
TAR="$OUT/nfupgrader-$VER.tar.gz"
# Ship the tool only: the tests stay in the repo.
git archive --format=tar --prefix="nfupgrader-$VER/" "$REF" -- . ':!tests' ':!test' |
	gzip -n > "$TAR"
SUM=$(sha256sum "$TAR" | cut -d' ' -f1)

sed -e "s#@VERSION@#$VER#g" -e "s#@SHA256@#$SUM#g" -e "s#@REPO@#$REPO#g" \
	tools/install.sh > "$OUT/install.sh"
if grep -q '@[A-Z0-9]*@' "$OUT/install.sh"; then
	echo "install.sh still has placeholders" >&2; exit 1
fi

echo "Built $TAR"
echo "      $OUT/install.sh  (sha256 $SUM)"
echo
echo "Publish with:"
echo "  gh release create v$VER --repo $REPO --title \"nfupgrader $VER\" --latest \\"
echo "      \"$TAR\" \"$OUT/install.sh\""
echo
echo "Install on a box (as root):"
echo "  curl -fsSL https://github.com/$REPO/releases/latest/download/install.sh | bash"
