#!/bin/bash
# install.sh — install or update nfupgrader on this host.
#
#   curl -fsSL https://github.com/@REPO@/releases/latest/download/install.sh | bash
#
# Run as root on RHEL/Rocky 7, 8 or 9 with network access to GitHub. Downloads
# nfupgrader-@VERSION@.tar.gz from the same release, checks its SHA-256, and
# unpacks it into /opt/nfupgrader. An existing install is updated in place:
# files from the release overwrite the old ones; logs/ (audit log, saved
# configs, report snapshots), any netFLEX-CORE*.rpm and anything else you put
# there are left alone. It does not start the tool.
#
# This file is a template: tools/make_release.sh fills in the version, the
# tarball's checksum and the GitHub repo.
#
# Test/ops overrides: NFU_INSTALL_DIR (default /opt/nfupgrader), NFU_TARBALL
# (a local tarball instead of downloading), NFU_ALLOW_NONROOT=1.
set -u

VERSION="@VERSION@"
SHA256="@SHA256@"
URL="https://github.com/@REPO@/releases/download/v${VERSION}/nfupgrader-${VERSION}.tar.gz"
DEST="${NFU_INSTALL_DIR:-/opt/nfupgrader}"

die() { echo "nfupgrader install: $*" >&2; exit 1; }
say() { echo "nfupgrader install: $*"; }

# --- pre-checks --------------------------------------------------------------
if [ "$(id -u)" -ne 0 ] && [ "${NFU_ALLOW_NONROOT:-}" != "1" ]; then
	die "run as root (the tool manages netFLEX as root)"
fi
if [ -r /etc/redhat-release ]; then
	grep -Eq 'release [789]([. ]|$)' /etc/redhat-release ||
		die "unsupported OS: $(cat /etc/redhat-release) (need RHEL/Rocky 7, 8 or 9)"
else
	die "not a RHEL/Rocky host (/etc/redhat-release missing)"
fi
for t in python3 tar sha256sum; do
	command -v "$t" >/dev/null 2>&1 || die "$t is required but not installed"
done
command -v ksh >/dev/null 2>&1 ||
	say "warning: ksh is not installed (the tool's pre-flight checks can install it)"

# --- fetch and verify ----------------------------------------------------------
TMP=$(mktemp -d "${TMPDIR:-/tmp}/nfupgrader-install.XXXXXX") || die "mktemp failed"
trap 'rm -rf "$TMP"' EXIT
TAR="$TMP/nfupgrader-${VERSION}.tar.gz"
if [ -n "${NFU_TARBALL:-}" ]; then
	cp "$NFU_TARBALL" "$TAR" || die "cannot read $NFU_TARBALL"
elif command -v curl >/dev/null 2>&1; then
	say "downloading $URL"
	curl -fsSL -o "$TAR" "$URL" || die "download failed: $URL"
elif command -v wget >/dev/null 2>&1; then
	say "downloading $URL"
	wget -q -O "$TAR" "$URL" || die "download failed: $URL"
else
	die "curl or wget is required to download the release"
fi
GOT=$(sha256sum "$TAR" | cut -d' ' -f1)
[ "$GOT" = "$SHA256" ] ||
	die "checksum mismatch for nfupgrader-${VERSION}.tar.gz (got $GOT, want $SHA256); nothing installed"

# --- unpack and install --------------------------------------------------------
tar -xzf "$TAR" -C "$TMP" || die "cannot unpack the release"
SRC="$TMP/nfupgrader-${VERSION}"
[ -f "$SRC/run.sh" ] || die "release tarball is missing run.sh"
if [ -e "$DEST" ]; then
	say "updating $DEST (logs/ and RPMs are kept)"
else
	say "installing into $DEST"
fi
mkdir -p "$DEST" || die "cannot create $DEST"
cp -a "$SRC/." "$DEST/" || die "copy into $DEST failed"
echo "$VERSION" > "$DEST/VERSION"

say "nfupgrader $VERSION installed in $DEST"
echo
echo "Start it with:"
echo "  cd $DEST && ./run.sh"
echo "(add NFU_BIND=0.0.0.0 NFU_PORT=8099 to reach it from another host; see README.md)"
