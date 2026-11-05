# nf_patch_test.ksh — sandboxed tests for nf_patch. Never touches real
# /usr/cnc, /usr/cnc_stage or the rpm database: roots come from NFP_*
# overrides and rpm/ps/hostname are stubs in $SB/bin, first on PATH.
#
# The rpm stub is driven by files in $SB:
#   rpmdb_core       "VERSION ARCH" of the installed netFLEX-CORE (absent = none)
#   rpmdb_patch      installed netFLEX-CORE-PATCH versions, one per line
#   rpmdb_ipatch     exists = netFLEX-CORE-IPATCH installed
#   meta/<basename>  "NAME VERSION ARCH" answered by rpm -qp (absent = rpm fails)
#   scripts/<base>   answered by rpm -qp --scripts
#   ps               output of the ps stub
DIR="$(cd "$(dirname "$0")/.." && pwd)"
. "${DIR}/test/nf_test_lib.ksh"

NFP="${DIR}/nf_patch"
ORIG_PATH="${PATH}"
SB=""

# mkstub <name> <body...> — write an executable stub that logs "<name> args"
# to $SB/calls, then runs body.
mkstub() {
	typeset name="$1"; shift
	{
		print -r -- '#!/bin/sh'
		print -r -- 'echo "$(basename "$0") $*" >> "${NFT_SB}/calls"'
		print -r -- "$*"
	} > "${SB}/bin/${name}"
	chmod +x "${SB}/bin/${name}"
}

setup() {
	cleanup
	SB=$(mktemp -d "${TMPDIR:-/tmp}/nfp_test.XXXXXX") || { print "mktemp failed"; exit 2; }
	mkdir -p "${SB}/logs" "${SB}/usr/cnc" "${SB}/bin" "${SB}/depot" "${SB}/meta" "${SB}/scripts"
	: > "${SB}/calls"
	: > "${SB}/ps"
	print "5.4.1.28 i686" > "${SB}/rpmdb_core"
	mkstub hostname 'echo h1'
	mkstub ps 'cat "${NFT_SB}/ps"'
	mkstub rpm '
for last in "$@"; do :; done
case "$1" in
-q)
	case "${last}" in
	netFLEX-CORE)
		[ -s "${NFT_SB}/rpmdb_core" ] || { echo "package netFLEX-CORE is not installed"; exit 1; }
		cat "${NFT_SB}/rpmdb_core";;
	netFLEX-CORE-PATCH)
		[ -s "${NFT_SB}/rpmdb_patch" ] || { echo "package netFLEX-CORE-PATCH is not installed"; exit 1; }
		cat "${NFT_SB}/rpmdb_patch";;
	netFLEX-CORE-IPATCH)
		[ -f "${NFT_SB}/rpmdb_ipatch" ] || { echo "package netFLEX-CORE-IPATCH is not installed"; exit 1; }
		echo "netFLEX-CORE-IPATCH-5.4.1.31-1.i686";;
	*) exit 1;;
	esac
	exit 0;;
-qp)
	m="${NFT_SB}/meta/$(basename "${last}")"
	[ -f "${m}" ] || { echo "error: open of ${last} failed" >&2; exit 1; }
	if [ "$2" = "--scripts" ]; then
		s="${NFT_SB}/scripts/$(basename "${last}")"
		[ -f "${s}" ] && cat "${s}"
		exit 0
	fi
	cat "${m}"; exit 0;;
-Uvh)
	echo "Preparing..."
	rc=${FAIL_RPM:-0}
	if [ "${rc}" -eq 0 ]; then
		set -- $(cat "${NFT_SB}/meta/$(basename "${last}")")
		echo "$2" > "${NFT_SB}/rpmdb_patch"
	fi
	exit ${rc};;
esac
exit 1'
	PATH="${SB}/bin:${ORIG_PATH}"
	export PATH
	export NFT_SB="${SB}"
	export NFP_INCROOT="${SB}/usr/cnc"
	export NFP_STAGEROOT="${SB}/usr/cnc_stage"
	export NFP_SKIP_ROOT=YES
	unset FAIL_RPM INCLOGDIR
}

cleanup() {
	[ -n "${SB}" ] && [ -d "${SB}" ] && rm -rf "${SB}"
	SB=""
	PATH="${ORIG_PATH}"
}

# patchfile <version> [arch] [name] — create a depot rpm and its -qp metadata;
# prints its path.
patchfile() {
	typeset v="$1" a="${2:-i686}" n="${3:-netFLEX-CORE-PATCH}"
	typeset f="${SB}/depot/${n}-${v}-1.rhel8.${a}.rpm"
	: > "${f}"
	print "${n} ${v} ${a}" > "${SB}/meta/$(basename "${f}")"
	print -r -- "${f}"
}

trace()    { cat "${SB}"/logs/patch_inc_h1.* 2>/dev/null; }
lastline() { trace | sed -n '$p'; }
uvh_calls() { grep -c -- '^rpm -Uvh' "${SB}/calls" | tr -d ' '; }

# run_patch <args...> — run nf_patch with the sandbox log dir; sets rc.
run_patch() {
	"${NFP}" -l "${SB}/logs" "$@"; rc=$?
}

# refused <label> <ere> — common assertions for a refusal.
refused() {
	assert_rc    "$1: rc" 1 "$rc"
	assert_match "$1: result" "ERROR RESULT: refused: .*$2" "$(lastline)"
	assert_eq    "$1: rpm -Uvh not run" 0 "$(uvh_calls)"
}

# --- first patch, none installed --------------------------------------------
setup
f29=$(patchfile 5.4.1.29); patchfile 5.4.1.30 >/dev/null
run_patch -f "${f29}"
assert_rc    "first patch: rc" 0 "$rc"
assert_match "first patch: result" 'INFO  RESULT: installed 5\.4\.1\.29$' "$(lastline)"
assert_match "first patch: rpm -Uvh called" "^rpm -Uvh ${f29}\$" "$(cat "${SB}/calls")"
assert_match "first patch: rpm output in trace" 'EXEC  \[rpm\] Preparing' "$(trace)"
assert_eq    "first patch: trace file name" 1 "$(ls "${SB}"/logs/patch_inc_h1.* 2>/dev/null | wc -l | tr -d ' ')"

# --- not the lowest in its directory ----------------------------------------
setup
patchfile 5.4.1.29 >/dev/null; f30=$(patchfile 5.4.1.30)
run_patch -f "${f30}"
refused "not lowest" 'install 5\.4\.1\.29 first$'

# --- strict next when a patch is installed ----------------------------------
setup
print 5.4.1.30 > "${SB}/rpmdb_patch"
f32=$(patchfile 5.4.1.32)
run_patch -f "${f32}"
refused "strict next" 'install 5\.4\.1\.31 first$'

# --- already installed -------------------------------------------------------
setup
print 5.4.1.30 > "${SB}/rpmdb_patch"
f30=$(patchfile 5.4.1.30)
run_patch -f "${f30}"
refused "already installed" 'already installed'

# --- wrong release -----------------------------------------------------------
setup
f=$(patchfile 5.4.2.29)
run_patch -f "${f}"
refused "wrong release" 'release 5\.4\.2 does not match installed 5\.4\.1$'

# --- wrong arch --------------------------------------------------------------
setup
f=$(patchfile 5.4.1.29 x86_64)
run_patch -f "${f}"
refused "wrong arch" 'arch x86_64 does not match installed i686$'

# --- unsupported package (IPATCH file) ---------------------------------------
setup
f=$(patchfile 5.4.1.29 i686 netFLEX-CORE-IPATCH)
run_patch -f "${f}"
refused "unsupported ipatch file" 'not a netFLEX-CORE-PATCH package'

# --- unreadable: rpm cannot read it / file missing ----------------------------
setup
f=$(patchfile 5.4.1.29); rm -f "${SB}/meta/$(basename "${f}")"
run_patch -f "${f}"
refused "unreadable rpm" 'rpm could not read'
setup
run_patch -f "${SB}/depot/nonexistent.rpm"
refused "missing file" 'cannot read '

# --- netFLEX-CORE not installed ----------------------------------------------
setup
rm -f "${SB}/rpmdb_core"
f=$(patchfile 5.4.1.29)
run_patch -f "${f}"
refused "no core" 'netFLEX-CORE is not installed$'

# --- IPATCH installed --------------------------------------------------------
setup
touch "${SB}/rpmdb_ipatch"
f=$(patchfile 5.4.1.29)
run_patch -f "${f}"
refused "ipatch installed" 'netFLEX-CORE-IPATCH.*contact support'

# --- staged upgrade present (dir, then link) ---------------------------------
setup
mkdir -p "${SB}/usr/cnc_stage"
f=$(patchfile 5.4.1.29)
run_patch -f "${f}"
refused "stage dir" 'staged upgrade'
setup
ln -s "${SB}/nowhere" "${SB}/usr/cnc_stage"
f=$(patchfile 5.4.1.29)
run_patch -f "${f}"
refused "stage dangling link" 'staged upgrade'

# --- upgrade lock --------------------------------------------------------------
setup
touch "${SB}/usr/cnc/.UPGRADE_LOCK"
f=$(patchfile 5.4.1.29)
run_patch -f "${f}"
refused "upgrade lock" 'upgrade is in progress'

# --- another tool running ------------------------------------------------------
setup
print '  4242 /usr/bin/ksh /opt/nfupgrader/nf_upgrader -u' > "${SB}/ps"
f=$(patchfile 5.4.1.29)
run_patch -f "${f}"
refused "other running" 'nf_upgrader is running'
setup
print '  4243 vi /tmp/nf_patch_notes' > "${SB}/ps"
f=$(patchfile 5.4.1.29)
run_patch -f "${f}"
assert_rc    "unrelated process: rc" 0 "$rc"

# --- reboot-required patch while netFLEX is up --------------------------------
setup
f=$(patchfile 5.4.1.29)
print 'preinstall scriptlet (using /bin/sh):\n    REBOOT_LOADS="29"\nfi' > "${SB}/scripts/$(basename "${f}")"
touch "${SB}/usr/cnc/.CNC_UP"
run_patch -f "${f}"
refused "needs stop" 'netFLEX must be stopped'
# same patch with netFLEX down installs
setup
f=$(patchfile 5.4.1.29)
print '    REBOOT_LOADS="29"' > "${SB}/scripts/$(basename "${f}")"
run_patch -f "${f}"
assert_rc    "reboot load, app down: rc" 0 "$rc"
# reboot load already passed (current 30 >= 29, installing 31) does not apply
setup
print 5.4.1.30 > "${SB}/rpmdb_patch"
f=$(patchfile 5.4.1.31)
print '    REBOOT_LOADS="29"' > "${SB}/scripts/$(basename "${f}")"
touch "${SB}/usr/cnc/.CNC_UP"
run_patch -f "${f}"
assert_rc    "reboot load passed: rc" 0 "$rc"
assert_match "reboot load passed: result" 'RESULT: installed 5\.4\.1\.31$' "$(lastline)"

# --- dry run -------------------------------------------------------------------
setup
f=$(patchfile 5.4.1.29)
run_patch -f "${f}" -n
assert_rc    "dry run: rc" 0 "$rc"
assert_match "dry run: result" 'INFO  RESULT: eligible 5\.4\.1\.29$' "$(lastline)"
assert_eq    "dry run: rpm -Uvh not run" 0 "$(uvh_calls)"

# --- rpm -Uvh fails --------------------------------------------------------------
setup
f=$(patchfile 5.4.1.29)
export FAIL_RPM=1
run_patch -f "${f}"
unset FAIL_RPM
assert_rc    "rpm fails: rc" 2 "$rc"
assert_match "rpm fails: result" 'ERROR RESULT: failed: rpm -Uvh exited 1$' "$(lastline)"

# --- post-install verify fails ---------------------------------------------------
setup
f=$(patchfile 5.4.1.29)
# rpm "succeeds" but leaves the database unchanged
sed -i 's/echo "\$2" > "\${NFT_SB}\/rpmdb_patch"/:/' "${SB}/bin/rpm"
run_patch -f "${f}"
assert_rc    "verify fails: rc" 2 "$rc"
assert_match "verify fails: result" 'ERROR RESULT: failed: rpm reports .* after install' "$(lastline)"

# --- multiple installed PATCH versions: highest wins (RF 1) ---------------------
setup
printf '5.4.1.29\n5.4.1.30\n' > "${SB}/rpmdb_patch"
f=$(patchfile 5.4.1.31)
run_patch -f "${f}"
assert_rc    "multiple patch versions: rc" 0 "$rc"
assert_match "multiple patch versions: result" 'RESULT: installed 5\.4\.1\.31$' "$(lastline)"

# --- leading-zero loads (RF 2) ---------------------------------------------------
setup
print "5.5.0.07 i686" > "${SB}/rpmdb_core"
f=$(patchfile 5.5.0.08)
run_patch -f "${f}"
assert_rc    "leading zero: rc" 0 "$rc"
assert_match "leading zero: result" 'RESULT: installed 5\.5\.0\.08$' "$(lastline)"
setup
print "5.5.0.07 i686" > "${SB}/rpmdb_core"
print 5.5.0.08 > "${SB}/rpmdb_patch"
f=$(patchfile 5.5.0.09)
run_patch -f "${f}"
assert_rc    "leading zero next: rc" 0 "$rc"

# --- usage ---------------------------------------------------------------------------
setup
run_patch
refused "no -f" 'no patch file given'
setup
"${NFP}" -z >/dev/null 2>&1; rc=$?
assert_rc    "bad option: rc" 1 "$rc"
"${NFP}" -h >/dev/null 2>&1; rc=$?
assert_rc    "help: rc" 0 "$rc"

cleanup
nf_test_summary
