# nf_install_test.ksh — sandboxed tests for nf_install. Never touches real
# /usr/cnc, /usr4, rpm or dnf: roots come from NFI_* overrides and every
# external command nf_install runs is a stub in $SB/bin, first on PATH.
DIR="$(cd "$(dirname "$0")/.." && pwd)"
. "${DIR}/test/nf_test_lib.ksh"

NFI="${DIR}/nf_install"
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

setup() {   # setup [INSTALLWEBGUI]
	cleanup
	SB=$(mktemp -d "${TMPDIR:-/tmp}/nfi_test.XXXXXX") || { print "mktemp failed"; exit 2; }
	mkdir -p "${SB}/logs" "${SB}/usr4" "${SB}/usr" "${SB}/bin"
	: > "${SB}/calls"
	print "SITE=test" > "${SB}/cnc.cnfg"
	: > "${SB}/netFLEX-CORE-5.3.0.02-1.x86_64.rpm"
	cat > "${SB}/test.cfg" <<EOF
INCLOGDIR=${SB}/logs
NEW_GENERIC=5.3.0
NEW_LOAD=02
DEPOTFILE=${SB}/netFLEX-CORE-5.3.0.02-1.x86_64.rpm
CNCCNFG=${SB}/cnc.cnfg
INSTALLWEBGUI=${1:-YES}
EOF
	mkstub hostname 'echo h1'
	mkstub mountpoint 'exit 0'
	mkstub chown 'exit 0'
	mkstub su 'exec sh -c "$3"'
	mkstub rpm '
case "$1" in
-q)  [ -f "${NFT_SB}/rpmdb" ] || exit 1
     [ "$2" = "--qf" ] && echo "5.3.0.02-1"
     exit 0;;
-qp) echo "5.3.0.02-1"; exit 0;;
-e)  rm -f "${NFT_SB}/rpmdb"; exit 0;;
esac
exit 1'
	# dnf "installs" the RPM into $NFI_STAGEROOT (which resolves to the load dir).
	mkstub dnf '
touch "${NFT_SB}/rpmdb"
R="${NFI_STAGEROOT}"
mkdir -p "$R/bin" "$R/mbin" "$R/features" "$R/GUI_REL" "${NFT_SB}/tarsrc"
mk() { printf "#!/bin/sh\necho \"\$(basename \"\$0\") \$*\" >> \"${NFT_SB}/calls\"\n%s\n" "$2" > "$1"; chmod +x "$1"; }
mk "$R/bin/linux_packages" "exit 0"
mk "$R/bin/cmmperm" "exit 0"
mk "$R/bin/post-install.sh" "exit \${FAIL_POSTINSTALL:-0}"
mk "$R/bin/nf_upgrade_pg.sh" "exit 0"
mk "$R/mbin/license-migrate" "exit \${FAIL_LICMIG:-0}"
W="${NFI_INCROOT}/web"
mk "${NFT_SB}/tarsrc/lwebsetup" "read a; read b; mkdir -p $W/data $W/maps $W/maps.default $W/bin; echo m > $W/maps.default/m1; echo s > $W/data/def.sys_config.flx; echo d > $W/data/def.system_defines.flx; printf \"#!/bin/sh\\nexit 0\\n\" > $W/bin/cpjsmap.osm; chmod +x $W/bin/cpjsmap.osm"
tar cf "$R/GUI_REL/LINUXWEB64.rhel8.tar" -C "${NFT_SB}/tarsrc" lwebsetup
exit 0'
	PATH="${SB}/bin:${ORIG_PATH}"
	export PATH
	export NFT_SB="${SB}"
	export NFI_FOREGROUND=YES
	export NFI_INCROOT="${SB}/usr/cnc"
	export NFI_STAGEROOT="${SB}/usr/cnc_stage"
	export NFI_LOADROOT="${SB}/usr4"
	export NFI_PKGTOOL="${SB}/bin/dnf"
	unset NFI_CHILD FAIL_POSTINSTALL FAIL_LICMIG
}

cleanup() {
	[ -n "${SB}" ] && [ -d "${SB}" ] && rm -rf "${SB}"
	SB=""
	PATH="${ORIG_PATH}"
}

state()  { cat "${SB}/logs/.h1_INSTALL_STATE" 2>/dev/null; }
trace()  { cat "${SB}"/logs/install_inc_h1.* 2>/dev/null; }
# Ordered names of the interesting commands that ran.
seq_of() {
	awk '{print $1}' "${SB}/calls" \
	| grep -E '^(dnf|linux_packages|cmmperm|license-migrate|post-install\.sh|nf_upgrade_pg\.sh|lwebsetup)$' \
	| tr '\n' ' '
}
count_of() { awk -v n="$1" '$1 == n' "${SB}/calls" | wc -l | tr -d ' '; }
exists() { if [ -e "$1" ] || [ -L "$1" ]; then print yes; else print no; fi; }

# --- fresh install ----------------------------------------------------------
setup
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "fresh install: rc" 0 "$rc"
assert_eq    "fresh install: state" isdone "$(state)"
assert_eq    "fresh install: /usr/cnc link" "${SB}/usr4/inc5.3.002" "$(readlink "${SB}/usr/cnc")"
assert_eq    "fresh install: cnc_stage removed" no "$(exists "${SB}/usr/cnc_stage")"
assert_eq    "fresh install: cnc.cnfg copied" "SITE=test" "$(cat "${SB}/usr/cnc/features/cnc.cnfg" 2>/dev/null)"
assert_eq    "fresh install: call order" \
	"dnf linux_packages cmmperm license-migrate post-install.sh nf_upgrade_pg.sh lwebsetup " "$(seq_of)"
assert_match "fresh install: Interfaces" '^\[h1_i\]$' "$(cat "${SB}/usr/cnc/web/data/Interfaces" 2>/dev/null)"
assert_eq    "fresh install: no upgrade state" no "$(exists "${SB}/logs/.h1_STATE")"
assert_match "fresh install: trace has INFO" ' INFO  ' "$(trace)"
assert_eq    "fresh install: trace no FATAL" "" "$(trace | grep FATAL)"
assert_match "fresh install: info LOADDIR" "^LOADDIR=${SB}/usr4/inc5.3.002$" "$(cat "${SB}/logs/.h1_INSTALL_INFO" 2>/dev/null)"
assert_match "fresh install: info VERSION" '^VERSION=5\.3\.0\.02$' "$(cat "${SB}/logs/.h1_INSTALL_INFO" 2>/dev/null)"
assert_match "fresh install: info DEPOTFILE" '^DEPOTFILE=.*netFLEX-CORE-5\.3\.0\.02-1\.x86_64\.rpm$' "$(cat "${SB}/logs/.h1_INSTALL_INFO" 2>/dev/null)"

# --- refuse installed (R1: refusal leaves no state/info) -------------------
setup
mkdir -p "${SB}/usr/cnc"
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "refuse installed: rc" 1 "$rc"
assert_match "refuse installed: trace FATAL" ' FATAL ' "$(trace)"
assert_eq    "refuse installed: no dnf" 0 "$(count_of dnf)"
assert_eq    "refuse installed: no state" no "$(exists "${SB}/logs/.h1_INSTALL_STATE")"
assert_eq    "refuse installed: no info" no "$(exists "${SB}/logs/.h1_INSTALL_INFO")"

# --- refuse when rpm says installed ----------------------------------------
setup
touch "${SB}/rpmdb"
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "refuse rpm installed: rc" 1 "$rc"
assert_eq    "refuse rpm installed: no state" no "$(exists "${SB}/logs/.h1_INSTALL_STATE")"
assert_eq    "refuse rpm installed: no /usr/cnc" no "$(exists "${SB}/usr/cnc")"

# --- refuse when /usr4 not mounted -----------------------------------------
setup
mkstub mountpoint 'exit 1'
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "refuse unmounted: rc" 1 "$rc"
assert_match "refuse unmounted: trace FATAL" ' FATAL ' "$(trace)"
assert_eq    "refuse unmounted: no state" no "$(exists "${SB}/logs/.h1_INSTALL_STATE")"

# --- fail and resume ---------------------------------------------------------
setup
FAIL_POSTINSTALL=1 "${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "fail and resume: fail rc" 1 "$rc"
assert_eq    "fail and resume: fail state" ispostinstall "$(state)"
assert_match "fail and resume: fail FATAL" ' FATAL ' "$(trace)"
"${NFI}" -c "${SB}/test.cfg" -r; rc=$?
assert_rc    "fail and resume: resume rc" 0 "$rc"
assert_eq    "fail and resume: resume state" isdone "$(state)"
assert_eq    "fail and resume: dnf once" 1 "$(count_of dnf)"
assert_eq    "fail and resume: post-install twice" 2 "$(count_of post-install.sh)"

# --- licmig warn -------------------------------------------------------------
setup
FAIL_LICMIG=1 "${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "licmig warn: rc" 0 "$rc"
assert_match "licmig warn: WARN" ' WARN  .*license' "$(trace)"
assert_eq    "licmig warn: state" isdone "$(state)"

# --- webgui off --------------------------------------------------------------
setup NO
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "webgui off: rc" 0 "$rc"
assert_eq    "webgui off: no lwebsetup" 0 "$(count_of lwebsetup)"
assert_eq    "webgui off: state" isdone "$(state)"

# --- unfinished refuses -i ---------------------------------------------------
setup
print isrpm > "${SB}/logs/.h1_INSTALL_STATE"
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "unfinished refuses -i: rc" 1 "$rc"
assert_eq    "unfinished refuses -i: state" isrpm "$(state)"
assert_eq    "unfinished refuses -i: no dnf" 0 "$(count_of dnf)"

# --- resume with nothing to resume ------------------------------------------
setup
"${NFI}" -c "${SB}/test.cfg" -r; rc=$?
assert_rc    "resume without state: rc" 1 "$rc"
assert_eq    "resume without state: no state" no "$(exists "${SB}/logs/.h1_INSTALL_STATE")"

# --- stale done state archived ----------------------------------------------
setup
print isdone > "${SB}/logs/.h1_INSTALL_STATE"
print "LOADDIR=/old" > "${SB}/logs/.h1_INSTALL_INFO"
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "stale done state archived: rc" 0 "$rc"
assert_match "stale done state archived: archive" '\.h1_INSTALL_STATE\.[0-9]{14}$' "$(ls "${SB}"/logs/.h1_INSTALL_STATE.* 2>/dev/null)"
assert_match "stale done state archived: info archive" '\.h1_INSTALL_INFO\.[0-9]{14}$' "$(ls "${SB}"/logs/.h1_INSTALL_INFO.* 2>/dev/null)"
assert_eq    "stale done state archived: state" isdone "$(state)"

# --- backgrounding: without NFI_FOREGROUND it returns 0 at once --------------
setup
unset NFI_FOREGROUND
mkstub mountpoint 'exit 1'
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "background: parent rc" 0 "$rc"
i=0
while [ $i -lt 50 ] && [ -z "$(trace | grep FATAL)" ]; do sleep 0.1; i=$((i+1)); done
assert_match "background: child ran" ' FATAL ' "$(trace)"

# --- usage -------------------------------------------------------------------
setup
"${NFI}" -c "${SB}/test.cfg" >/dev/null 2>&1; rc=$?
assert_rc    "usage: no action" 1 "$rc"
"${NFI}" -c "${SB}/test.cfg" -i -r >/dev/null 2>&1; rc=$?
assert_rc    "usage: two actions" 1 "$rc"

cleanup
nf_test_summary
