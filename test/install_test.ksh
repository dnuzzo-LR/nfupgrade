# install_test.ksh — tools/make_release.sh + the generated install.sh, in a
# sandbox (never touches /opt). Run from the repo root: ksh test/install_test.ksh
DIR="$(cd "$(dirname "$0")/.." && pwd)"
. "${DIR}/test/nf_test_lib.ksh"

SB=$(mktemp -d "${TMPDIR:-/tmp}/nfu_inst.XXXXXX") || exit 2
trap 'rm -rf "$SB"' EXIT
VER=0.0.0-test
REL="$SB/dist"

# --- make_release: tarball, filled-in install.sh, checksum matches ----------
NFU_RELEASE_REF=HEAD NFU_RELEASE_OUT="$REL" NFU_RELEASE_REPO=example/nfupgrader \
	sh "$DIR/tools/make_release.sh" "$VER" > "$SB/mk.out" 2>&1
assert_rc "make_release rc" 0 "$?"
assert_eq "tarball built" "yes" "$([ -f "$REL/nfupgrader-$VER.tar.gz" ] && echo yes)"
assert_eq "install.sh built" "yes" "$([ -f "$REL/install.sh" ] && echo yes)"
assert_eq "no placeholders left" "" "$(grep -n '@[A-Z0-9]*@' "$REL/install.sh")"
SUM=$(sha256sum "$REL/nfupgrader-$VER.tar.gz" | cut -d' ' -f1)
assert_match "checksum embedded" "$SUM" "$(cat "$REL/install.sh")"
assert_match "repo embedded" "github.com/example/nfupgrader/releases/download/v" "$(cat "$REL/install.sh")"
assert_match "version embedded" "^VERSION=\"$VER\"$" "$(cat "$REL/install.sh")"
assert_match "tarball has top dir" "^nfupgrader-$VER/run.sh$" "$(tar -tzf "$REL/nfupgrader-$VER.tar.gz")"
assert_eq "tarball has no tests" "" "$(tar -tzf "$REL/nfupgrader-$VER.tar.gz" | grep -E '/tests?/')"

inst() {   # inst <dest> [env...] — run the generated installer on the local tarball
	typeset dest="$1"; shift
	env NFU_INSTALL_DIR="$dest" NFU_TARBALL="$REL/nfupgrader-$VER.tar.gz" \
		NFU_ALLOW_NONROOT=1 "$@" bash "$REL/install.sh" > "$SB/inst.out" 2>&1
}

# --- fresh install -----------------------------------------------------------
D="$SB/opt/nfupgrader"
inst "$D"
assert_rc "fresh rc" 0 "$?"
assert_eq "run.sh installed" "yes" "$([ -x "$D/run.sh" ] && echo yes)"
assert_eq "package installed" "yes" "$([ -f "$D/nfupgrader/httpd.py" ] && echo yes)"
assert_eq "VERSION written" "$VER" "$(cat "$D/VERSION")"
assert_match "says how to start" "cd $D && \./run\.sh" "$(cat "$SB/inst.out")"

# --- reinstall keeps logs/ and RPMs, overwrites tool files --------------------
mkdir -p "$D/logs" && echo audit > "$D/logs/nfupgrader_audit.jsonl"
echo rpm > "$D/netFLEX-CORE-9.9.9.99-1.rpm"
echo stale > "$D/nfupgrader/httpd.py"
inst "$D"
assert_rc "reinstall rc" 0 "$?"
assert_eq "logs kept" "audit" "$(cat "$D/logs/nfupgrader_audit.jsonl")"
assert_eq "rpm kept" "rpm" "$(cat "$D/netFLEX-CORE-9.9.9.99-1.rpm")"
assert_match "tool file replaced" "def main" "$(cat "$D/nfupgrader/httpd.py")"

# --- bad checksum: refuses, installs nothing ---------------------------------
cp "$REL/nfupgrader-$VER.tar.gz" "$SB/bad.tar.gz"
echo junk >> "$SB/bad.tar.gz"
D2="$SB/opt2/nfupgrader"
env NFU_INSTALL_DIR="$D2" NFU_TARBALL="$SB/bad.tar.gz" NFU_ALLOW_NONROOT=1 \
	bash "$REL/install.sh" > "$SB/bad.out" 2>&1
assert_rc "bad checksum rc" 1 "$?"
assert_match "bad checksum message" "checksum" "$(cat "$SB/bad.out")"
assert_eq "nothing installed" "no" "$([ -e "$D2" ] && echo yes || echo no)"

# --- not root: refused -------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
	D3="$SB/opt3/nfupgrader"
	env NFU_INSTALL_DIR="$D3" NFU_TARBALL="$REL/nfupgrader-$VER.tar.gz" \
		bash "$REL/install.sh" > "$SB/nr.out" 2>&1
	assert_rc "non-root rc" 1 "$?"
	assert_match "non-root message" "root" "$(cat "$SB/nr.out")"
	assert_eq "non-root installs nothing" "no" "$([ -e "$D3" ] && echo yes || echo no)"
fi

# --- missing python3: refused -------------------------------------------------
mkdir -p "$SB/nopy"
for t in tar sha256sum mktemp id uname cat rm cp mkdir dirname grep sed cut; do
	p=$(command -v $t) && ln -s "$p" "$SB/nopy/$t"
done
D4="$SB/opt4/nfupgrader"
env PATH="$SB/nopy" NFU_INSTALL_DIR="$D4" NFU_TARBALL="$REL/nfupgrader-$VER.tar.gz" \
	NFU_ALLOW_NONROOT=1 "$(command -v bash)" "$REL/install.sh" > "$SB/np.out" 2>&1
assert_rc "no python3 rc" 1 "$?"
assert_match "no python3 message" "python3" "$(cat "$SB/np.out")"

nf_test_summary
