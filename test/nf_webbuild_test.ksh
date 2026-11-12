# nf_webbuild_test.ksh — tests for nf_webbuild against real, small web tars
# built in a sandbox (lwebdata.Z = gzip of a cpio holding a fake
# web/bin/webinfo). NFP_INCROOT points the temp dir into the sandbox.
DIR="$(cd "$(dirname "$0")/.." && pwd)"
. "${DIR}/test/nf_test_lib.ksh"

NFW="${DIR}/nf_webbuild"
SB=""

# mkwebtar <path> <release> <build> [lwebsetup-body] — build a LINUXWEB64-style
# tar: top-level lwebsetup + lwebdata.Z (gzip'd cpio of web/ with webinfo -j).
# NFT_NOEXEC=1 leaves webinfo non-executable; NFT_NODATA=1 omits lwebdata.Z.
mkwebtar() {
	typeset path="$1" rel="$2" bld="$3" body="${4:-exit 0}" w
	w=$(mktemp -d "${SB}/mk.XXXXXX")
	mkdir -p "${w}/web/bin"
	print "#!/bin/sh\necho '{ \"release\" : \"${rel}\", \"build\" : \"${bld}\"}'" > "${w}/web/bin/webinfo"
	[ "${NFT_NOEXEC:-0}" = 1 ] || chmod +x "${w}/web/bin/webinfo"
	print -r -- "#!/bin/sh
${body}" > "${w}/lwebsetup"
	chmod +x "${w}/lwebsetup"
	(cd "${w}" && find web | cpio -o > lwebdata 2>/dev/null && gzip -c lwebdata > lwebdata.Z)
	if [ "${NFT_NODATA:-0}" = 1 ]; then
		(cd "${w}" && tar cf "${path}" lwebsetup)
	else
		(cd "${w}" && tar cf "${path}" lwebdata.Z lwebsetup)
	fi
	rm -rf "${w}"
}

setup() {
	cleanup
	SB=$(mktemp -d "${TMPDIR:-/tmp}/nfw_test.XXXXXX") || { print "mktemp failed"; exit 2; }
	mkdir -p "${SB}/usr/cnc"
	export NFP_INCROOT="${SB}/usr/cnc"
}

cleanup() { [ -n "${SB}" ] && rm -rf "${SB}"; SB=""; }

# run <tar> — sets RC, OUT, ERR.
run() {
	"${NFW}" "$1" > "${SB}/out" 2> "${SB}/err"
	RC=$?
	OUT=$(cat "${SB}/out"); ERR=$(cat "${SB}/err")
}

notemp() {   # notemp <label>
	assert_eq "$1: no temp left" "" "$(ls -A "${SB}/usr/cnc" | grep '^\.nfweb')"
}

setup
mkwebtar "${SB}/LINUXWEB64.rhel8.tar" 5.4.0 36
run "${SB}/LINUXWEB64.rhel8.tar"
assert_rc "good: rc" 0 "${RC}"
assert_eq "good: stdout" '{ "release" : "5.4.0", "build" : "36"}' "${OUT}"
assert_eq "good: no stderr" "" "${ERR}"
notemp good

setup
NFT_NODATA=1; mkwebtar "${SB}/w.tar" 5.4.0 36; NFT_NODATA=0
run "${SB}/w.tar"
assert_rc "no lwebdata: rc" 1 "${RC}"
assert_match "no lwebdata: msg" '^nf_webbuild: no lwebdata\.Z in ' "${ERR}"
notemp "no lwebdata"

setup
print "not a tar" > "${SB}/w.tar"
run "${SB}/w.tar"
assert_rc "not a tar: rc" 1 "${RC}"
assert_match "not a tar: msg" '^nf_webbuild: cannot read ' "${ERR}"
notemp "not a tar"

setup
run "${SB}/missing.tar"
assert_rc "missing: rc" 1 "${RC}"
assert_match "missing: msg" '^nf_webbuild: cannot read ' "${ERR}"
notemp missing

setup
NFT_NOEXEC=1; mkwebtar "${SB}/w.tar" 5.4.0 36; NFT_NOEXEC=0
run "${SB}/w.tar"
assert_rc "noexec webinfo: rc" 1 "${RC}"
assert_match "noexec webinfo: msg" '^nf_webbuild: webinfo did not run' "${ERR}"
notemp "noexec webinfo"

setup
mkwebtar "${SB}/LINUXWEB64.rhel8.tar" 5.4.0 36
OUT=$(cd "${SB}" && "${NFW}" LINUXWEB64.rhel8.tar 2>&1); RC=$?
assert_rc "relative path: rc" 0 "${RC}"
assert_eq "relative path: stdout" '{ "release" : "5.4.0", "build" : "36"}' "${OUT}"

# a run killed by the service's timeout (SIGKILL: no trap) leaves its temp
# dir; the next run removes temp dirs older than an hour
setup
mkdir -p "${SB}/usr/cnc/.nfweb.old1" "${SB}/usr/cnc/.nfweb.new1"
touch -t 202001010000 "${SB}/usr/cnc/.nfweb.old1"
mkwebtar "${SB}/w.tar" 5.4.0 36
run "${SB}/w.tar"
assert_rc "stale temp: rc" 0 "${RC}"
assert_eq "stale temp: old removed, recent kept" ".nfweb.new1" "$(ls -A "${SB}/usr/cnc" | grep '^\.nfweb')"

setup
run ""
assert_rc "no arg: rc" 1 "${RC}"
assert_match "no arg: usage" 'usage' "${ERR}"

cleanup
nf_test_summary
