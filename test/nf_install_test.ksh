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
	mkstub apachectl 'exit ${FAIL_APACHECTL:-0}'
	mkstub systemctl '[ "$1" = is-active ] && exit ${HTTPD_DOWN:-0}; exit 0'
	mkdir -p "${SB}/conf.d"
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
printf "# INC_QUEUE_SIZE is used in Solaris\nOTHER=1\nINC_QUEUE_SIZE=%s\nLAST=2\n" "${STUB_IQS:-1048576}" > "$R/features/system_defines"
W="${NFI_INCROOT}/web"
mk "${NFT_SB}/tarsrc/lwebsetup" "read a; read b; mkdir -p $W/data $W/maps $W/maps.default $W/bin; echo m > $W/maps.default/m1; echo s > $W/data/def.sys_config.flx; echo d > $W/data/def.system_defines.flx; [ -n \"\${NO_CPJSMAP}\" ] || { printf \"#!/bin/sh\\nexit 0\\n\" > $W/bin/cpjsmap.osm; chmod +x $W/bin/cpjsmap.osm; }"
tar cf "$R/GUI_REL/LINUXWEB64.rhel8.tar" -C "${NFT_SB}/tarsrc" lwebsetup
# Simulate something replacing the stage link after the RPM went in.
if [ -n "${STAGE_REAL}" ]; then rm -f "$R"; mkdir -p "$R/keep"; fi
if [ -n "${STAGE_FOREIGN}" ]; then rm -f "$R"; mkdir -p "${NFT_SB}/usr4/other"; ln -s "${NFT_SB}/usr4/other" "$R"; fi
exit 0'
	PATH="${SB}/bin:${ORIG_PATH}"
	export PATH
	export NFT_SB="${SB}"
	export NFI_FOREGROUND=YES
	export NFI_INCROOT="${SB}/usr/cnc"
	export NFI_STAGEROOT="${SB}/usr/cnc_stage"
	export NFI_LOADROOT="${SB}/usr4"
	export NFI_PKGTOOL="${SB}/bin/dnf"
	print 4194304 > "${SB}/msgmnb"
	export NFI_MSGMNB_FILE="${SB}/msgmnb"
	export NFI_HTTPD_CONFD="${SB}/conf.d"
	unset NFI_CHILD FAIL_POSTINSTALL FAIL_LICMIG STAGE_REAL STAGE_FOREIGN NO_CPJSMAP STUB_IQS \
		FAIL_APACHECTL HTTPD_DOWN
}

cleanup() {
	[ -n "${SB}" ] && [ -d "${SB}" ] && rm -rf "${SB}"
	SB=""
	PATH="${ORIG_PATH}"
}

state()  { cat "${SB}/logs/.h1_INSTALL_STATE" 2>/dev/null; }
trace()  { cat "${SB}"/logs/install_inc_h1.* 2>/dev/null; }
rtrace() { cat "${SB}"/logs/reset_inc_h1.* 2>/dev/null; }
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
	"dnf linux_packages cmmperm license-migrate post-install.sh nf_upgrade_pg.sh cmmperm lwebsetup " "$(seq_of)"
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

# --- web GUI without cpjsmap.osm (not shipped in netFLEX 5.5): warn, finish ---
setup
NO_CPJSMAP=1 "${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "no cpjsmap: rc" 0 "$rc"
assert_eq    "no cpjsmap: state" isdone "$(state)"
assert_match "no cpjsmap: WARN" ' WARN  .*cpjsmap\.osm not found' "$(trace)"
assert_eq    "no cpjsmap: web GUI not failed" "" "$(trace | grep 'Web GUI install failed')"
assert_eq    "no cpjsmap: sys_config" s "$(cat "${SB}/usr/cnc/web/data/sys_config" 2>/dev/null)"
assert_eq    "no cpjsmap: system_defines" d "$(cat "${SB}/usr/cnc/web/data/system_defines" 2>/dev/null)"
assert_match "no cpjsmap: Interfaces" '^\[h1_i\]$' "$(cat "${SB}/usr/cnc/web/data/Interfaces" 2>/dev/null)"

# --- INC_QUEUE_SIZE raised to kernel.msgmnb (boot_nms refuses to boot below it) ---
iqs() { sed -n 's/^INC_QUEUE_SIZE=//p' "${SB}/usr/cnc/features/system_defines"; }
setup
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "inc queue: rc" 0 "$rc"
assert_eq    "inc queue: raised to msgmnb" 4194304 "$(iqs)"
assert_match "inc queue: other lines kept" '^OTHER=1$' "$(cat "${SB}/usr/cnc/features/system_defines")"
assert_match "inc queue: comment kept" 'INC_QUEUE_SIZE is used in Solaris' "$(cat "${SB}/usr/cnc/features/system_defines")"
assert_match "inc queue: logged" 'INC_QUEUE_SIZE.*4194304' "$(trace)"

setup
print 8388608 > "${SB}/msgmnb"
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_eq    "inc queue: follows a larger msgmnb" 8388608 "$(iqs)"

setup
STUB_IQS=9000000 "${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_eq    "inc queue: higher value left alone" 9000000 "$(iqs)"

setup
print junk > "${SB}/msgmnb"
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_eq    "inc queue: unreadable msgmnb uses 4194304" 4194304 "$(iqs)"

# --- Interfaces uses the short host name (the GUI splits keys at the first dot) ---
setup
mkstub hostname 'echo h1.example.com'
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "fqdn host: rc" 0 "$rc"
assert_eq    "fqdn host: Interfaces short name" "[h1_i]
   host=h1
   protocol=ssh" "$(cat "${SB}/usr/cnc/web/data/Interfaces" 2>/dev/null)"

# --- web GUI Apache grant (webgui.conf grants nothing; stock httpd.conf denies /) ---
WA() { print "${SB}/conf.d/nfupgrader-webgui.conf"; }
setup
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "web access: rc" 0 "$rc"
assert_match "web access: marker" '^# Written by nf_install \(nfupgrader\)\.$' "$(cat "$(WA)" 2>/dev/null)"
assert_match "web access: grants web dir" "^<Directory ${SB}/usr/cnc/web>\$" "$(cat "$(WA)" 2>/dev/null)"
assert_match "web access: require granted" '^    Require all granted$' "$(cat "$(WA)" 2>/dev/null)"
assert_eq    "web access: configtest ran" 1 "$(awk '$1 == "apachectl" && $2 == "configtest"' "${SB}/calls" | wc -l | tr -d ' ')"
assert_eq    "web access: httpd reloaded" 1 "$(awk '$1 == "systemctl" && $2 == "reload" && $3 == "httpd"' "${SB}/calls" | wc -l | tr -d ' ')"
assert_match "web access: logged" ' INFO  .*Granted Apache access' "$(trace)"

setup
FAIL_APACHECTL=1 "${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "web access bad configtest: rc" 0 "$rc"
assert_eq    "web access bad configtest: state" isdone "$(state)"
assert_eq    "web access bad configtest: file removed" no "$(exists "$(WA)")"
assert_match "web access bad configtest: WARN" ' WARN  .*configtest failed' "$(trace)"
assert_eq    "web access bad configtest: no reload" 0 "$(awk '$1 == "systemctl" && $2 == "reload"' "${SB}/calls" | wc -l | tr -d ' ')"

setup
HTTPD_DOWN=3 "${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "web access httpd down: rc" 0 "$rc"
assert_eq    "web access httpd down: file kept" yes "$(exists "$(WA)")"
assert_match "web access httpd down: WARN" ' WARN  .*httpd is not running' "$(trace)"
assert_eq    "web access httpd down: no reload" 0 "$(awk '$1 == "systemctl" && $2 == "reload"' "${SB}/calls" | wc -l | tr -d ' ')"

setup
rmdir "${SB}/conf.d"
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "web access no conf.d: rc" 0 "$rc"
assert_match "web access no conf.d: WARN" ' WARN  .*not granted' "$(trace)"

# --- webgui off --------------------------------------------------------------
setup NO
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "webgui off: rc" 0 "$rc"
assert_eq    "webgui off: no lwebsetup" 0 "$(count_of lwebsetup)"
assert_eq    "webgui off: state" isdone "$(state)"
assert_eq    "webgui off: no Apache grant" no "$(exists "${SB}/conf.d/nfupgrader-webgui.conf")"

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

# --- reset after failure -----------------------------------------------------
setup
FAIL_POSTINSTALL=1 "${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "reset after failure: install rc" 1 "$rc"
assert_eq    "reset after failure: rpm installed first" yes "$(exists "${SB}/rpmdb")"
# As if an earlier attempt got as far as the web GUI step.
printf '# Written by nf_install (nfupgrader).\n<Directory x>\n</Directory>\n' > "${SB}/conf.d/nfupgrader-webgui.conf"
"${NFI}" -c "${SB}/test.cfg" -x; rc=$?
assert_rc    "reset after failure: rc" 0 "$rc"
assert_eq    "reset after failure: Apache grant removed" no "$(exists "${SB}/conf.d/nfupgrader-webgui.conf")"
assert_eq    "reset after failure: rpm -e ran" 1 "$(awk '$1 == "rpm" && $2 == "-e"' "${SB}/calls" | wc -l | tr -d ' ')"
assert_eq    "reset after failure: rpmdb gone" no "$(exists "${SB}/rpmdb")"
assert_eq    "reset after failure: /usr/cnc gone" no "$(exists "${SB}/usr/cnc")"
assert_eq    "reset after failure: cnc_stage gone" no "$(exists "${SB}/usr/cnc_stage")"
assert_eq    "reset after failure: load dir gone" no "$(exists "${SB}/usr4/inc5.3.002")"
assert_eq    "reset after failure: /usr4 kept" yes "$(exists "${SB}/usr4")"
assert_eq    "reset after failure: no state" no "$(exists "${SB}/logs/.h1_INSTALL_STATE")"
assert_eq    "reset after failure: no info" no "$(exists "${SB}/logs/.h1_INSTALL_INFO")"
assert_match "reset after failure: state archived" '\.h1_INSTALL_STATE\.[0-9]{14}$' "$(ls "${SB}"/logs/.h1_INSTALL_STATE.* 2>/dev/null)"
assert_match "reset after failure: info archived" '\.h1_INSTALL_INFO\.[0-9]{14}$' "$(ls "${SB}"/logs/.h1_INSTALL_INFO.* 2>/dev/null)"
assert_match "reset after failure: reset trace" 'reset_inc_h1\.[0-9]+$' "$(ls "${SB}"/logs/reset_inc_h1.* 2>/dev/null)"
assert_match "reset after failure: marker recorded" '^LOADDIR_CREATED=yes$' "$(cat "${SB}"/logs/.h1_INSTALL_INFO.* 2>/dev/null)"
assert_match "reset after failure: probe logged" 'rpm -q netFLEX-CORE \(rc 0\)' "$(rtrace)"
assert_match "reset after failure: reset complete" ' INFO  .*reset complete' "$(rtrace)"
assert_eq    "reset after failure: no FATAL" "" "$(rtrace | grep FATAL)"
assert_match "reset after failure: install trace kept" 'install_inc_h1\.[0-9]+$' "$(ls "${SB}"/logs/install_inc_h1.* 2>/dev/null)"
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "reset after failure: fresh install again rc" 0 "$rc"
assert_eq    "reset after failure: fresh install again state" isdone "$(state)"

# --- reset before the RPM went in: no rpm -e --------------------------------
setup
mkdir -p "${SB}/usr4/inc5.3.002"
ln -s "${SB}/usr4/inc5.3.002" "${SB}/usr/cnc"
ln -s "${SB}/usr4/inc5.3.002" "${SB}/usr/cnc_stage"
print isrpm > "${SB}/logs/.h1_INSTALL_STATE"
printf 'LOADDIR=%s\nLOADDIR_CREATED=yes\n' "${SB}/usr4/inc5.3.002" > "${SB}/logs/.h1_INSTALL_INFO"
print "# someone else's file" > "${SB}/conf.d/nfupgrader-webgui.conf"
"${NFI}" -c "${SB}/test.cfg" -x; rc=$?
assert_rc    "reset no rpm: rc" 0 "$rc"
assert_eq    "reset no rpm: unmarked Apache file kept" yes "$(exists "${SB}/conf.d/nfupgrader-webgui.conf")"
assert_match "reset no rpm: probe logged" 'rpm -q netFLEX-CORE \(rc 1\)' "$(rtrace)"
assert_eq    "reset no rpm: no rpm -e" 0 "$(awk '$1 == "rpm" && $2 == "-e"' "${SB}/calls" | wc -l | tr -d ' ')"
assert_eq    "reset no rpm: /usr/cnc gone" no "$(exists "${SB}/usr/cnc")"
assert_eq    "reset no rpm: cnc_stage gone" no "$(exists "${SB}/usr/cnc_stage")"
assert_eq    "reset no rpm: load dir gone" no "$(exists "${SB}/usr4/inc5.3.002")"
assert_eq    "reset no rpm: no state" no "$(exists "${SB}/logs/.h1_INSTALL_STATE")"

# --- reset keeps a pre-existing load dir (R5) --------------------------------
setup
mkdir -p "${SB}/usr4/inc5.3.002"
print keep > "${SB}/usr4/inc5.3.002/sentinel"
FAIL_POSTINSTALL=1 "${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "reset pre-existing dir: install rc" 1 "$rc"
assert_eq    "reset pre-existing dir: no marker" "" "$(grep LOADDIR_CREATED "${SB}/logs/.h1_INSTALL_INFO")"
"${NFI}" -c "${SB}/test.cfg" -x; rc=$?
assert_rc    "reset pre-existing dir: rc" 0 "$rc"
assert_eq    "reset pre-existing dir: load dir kept" yes "$(exists "${SB}/usr4/inc5.3.002")"
assert_eq    "reset pre-existing dir: sentinel kept" keep "$(cat "${SB}/usr4/inc5.3.002/sentinel" 2>/dev/null)"
assert_match "reset pre-existing dir: WARN" ' WARN  .*not created by this install' "$(rtrace)"
assert_eq    "reset pre-existing dir: rpmdb gone" no "$(exists "${SB}/rpmdb")"
assert_eq    "reset pre-existing dir: /usr/cnc gone" no "$(exists "${SB}/usr/cnc")"
assert_eq    "reset pre-existing dir: cnc_stage gone" no "$(exists "${SB}/usr/cnc_stage")"
assert_eq    "reset pre-existing dir: no state" no "$(exists "${SB}/logs/.h1_INSTALL_STATE")"
assert_match "reset pre-existing dir: state archived" '\.h1_INSTALL_STATE\.[0-9]{14}$' "$(ls "${SB}"/logs/.h1_INSTALL_STATE.* 2>/dev/null)"
assert_match "reset pre-existing dir: reset complete" 'reset complete' "$(rtrace)"

# --- resume keeps the marker, so a later reset still removes the dir (R5) ----
# Fail at post-install, force the state back to isprepare so the resume
# re-runs isprepare over the dir it created, and fail again.
setup
FAIL_POSTINSTALL=1 "${NFI}" -c "${SB}/test.cfg" -i; rc=$?
print isprepare > "${SB}/logs/.h1_INSTALL_STATE"
FAIL_POSTINSTALL=1 "${NFI}" -c "${SB}/test.cfg" -r; rc=$?
assert_rc    "resume keeps marker: resume rc" 1 "$rc"
assert_eq    "resume keeps marker: state" ispostinstall "$(state)"
assert_eq    "resume keeps marker: marker once" 1 "$(grep -c '^LOADDIR_CREATED=yes$' "${SB}/logs/.h1_INSTALL_INFO")"
assert_match "resume keeps marker: isprepare logged" 'already created by this install' "$(trace)"
"${NFI}" -c "${SB}/test.cfg" -x; rc=$?
assert_rc    "resume keeps marker: reset rc" 0 "$rc"
assert_eq    "resume keeps marker: load dir gone" no "$(exists "${SB}/usr4/inc5.3.002")"

# --- reset refuses foreign link ----------------------------------------------
setup
FAIL_POSTINSTALL=1 "${NFI}" -c "${SB}/test.cfg" -i; rc=$?
mkdir -p "${SB}/usr4/other"
rm -f "${SB}/usr/cnc"
ln -s "${SB}/usr4/other" "${SB}/usr/cnc"
"${NFI}" -c "${SB}/test.cfg" -x; rc=$?
assert_rc    "reset refuses foreign link: rc" 1 "$rc"
assert_match "reset refuses foreign link: FATAL" ' FATAL ' "$(rtrace)"
assert_eq    "reset refuses foreign link: rpmdb kept" yes "$(exists "${SB}/rpmdb")"
assert_eq    "reset refuses foreign link: no rpm -e" 0 "$(awk '$1 == "rpm" && $2 == "-e"' "${SB}/calls" | wc -l | tr -d ' ')"
assert_eq    "reset refuses foreign link: load dir kept" yes "$(exists "${SB}/usr4/inc5.3.002")"
assert_eq    "reset refuses foreign link: other kept" yes "$(exists "${SB}/usr4/other")"
assert_eq    "reset refuses foreign link: link kept" "${SB}/usr4/other" "$(readlink "${SB}/usr/cnc")"
assert_eq    "reset refuses foreign link: state" ispostinstall "$(state)"
assert_eq    "reset refuses foreign link: info kept" yes "$(exists "${SB}/logs/.h1_INSTALL_INFO")"
assert_eq    "reset refuses foreign link: no archive" "" "$(ls "${SB}"/logs/.h1_INSTALL_STATE.* 2>/dev/null)"

# --- reset refuses a real directory where a link should be -----------------
setup
FAIL_POSTINSTALL=1 "${NFI}" -c "${SB}/test.cfg" -i; rc=$?
mkdir -p "${SB}/usr/cnc_stage/keep"
"${NFI}" -c "${SB}/test.cfg" -x; rc=$?
assert_rc    "reset refuses non-link: rc" 1 "$rc"
assert_eq    "reset refuses non-link: dir kept" yes "$(exists "${SB}/usr/cnc_stage/keep")"
assert_eq    "reset refuses non-link: rpmdb kept" yes "$(exists "${SB}/rpmdb")"
assert_eq    "reset refuses non-link: /usr/cnc kept" "${SB}/usr4/inc5.3.002" "$(readlink "${SB}/usr/cnc")"
assert_eq    "reset refuses non-link: state" ispostinstall "$(state)"

# --- reset refuses when done -------------------------------------------------
setup
mkdir -p "${SB}/usr4/inc5.3.002"
print isdone > "${SB}/logs/.h1_INSTALL_STATE"
print "LOADDIR=${SB}/usr4/inc5.3.002" > "${SB}/logs/.h1_INSTALL_INFO"
"${NFI}" -c "${SB}/test.cfg" -x; rc=$?
assert_rc    "reset refuses when done: rc" 1 "$rc"
assert_match "reset refuses when done: FATAL" ' FATAL ' "$(rtrace)"
assert_eq    "reset refuses when done: load dir kept" yes "$(exists "${SB}/usr4/inc5.3.002")"
assert_eq    "reset refuses when done: state" isdone "$(state)"

# --- reset refuses with no state ---------------------------------------------
setup
mkdir -p "${SB}/usr4/inc5.3.002"
print "LOADDIR=${SB}/usr4/inc5.3.002" > "${SB}/logs/.h1_INSTALL_INFO"
"${NFI}" -c "${SB}/test.cfg" -x; rc=$?
assert_rc    "reset refuses no state: rc" 1 "$rc"
assert_eq    "reset refuses no state: load dir kept" yes "$(exists "${SB}/usr4/inc5.3.002")"
assert_eq    "reset refuses no state: info kept" yes "$(exists "${SB}/logs/.h1_INSTALL_INFO")"

# --- reset refuses a LOADDIR outside ${LOADROOT}/inc* -----------------------
setup
mkdir -p "${SB}/keepme"
print isrpm > "${SB}/logs/.h1_INSTALL_STATE"
print "LOADDIR=${SB}/keepme" > "${SB}/logs/.h1_INSTALL_INFO"
"${NFI}" -c "${SB}/test.cfg" -x; rc=$?
assert_rc    "reset refuses bad LOADDIR: rc" 1 "$rc"
assert_eq    "reset refuses bad LOADDIR: dir kept" yes "$(exists "${SB}/keepme")"
assert_eq    "reset refuses bad LOADDIR: state" isrpm "$(state)"

# --- reset refuses a LOADDIR that escapes via .. ----------------------------
setup
mkdir -p "${SB}/usr4/inc5" "${SB}/keepme"
print isrpm > "${SB}/logs/.h1_INSTALL_STATE"
print "LOADDIR=${SB}/usr4/inc5/../../keepme" > "${SB}/logs/.h1_INSTALL_INFO"
"${NFI}" -c "${SB}/test.cfg" -x; rc=$?
assert_rc    "reset refuses .. LOADDIR: rc" 1 "$rc"
assert_eq    "reset refuses .. LOADDIR: dir kept" yes "$(exists "${SB}/keepme")"
assert_eq    "reset refuses .. LOADDIR: state" isrpm "$(state)"

# --- reset stops when rpm -e fails -------------------------------------------
setup
FAIL_POSTINSTALL=1 "${NFI}" -c "${SB}/test.cfg" -i; rc=$?
mkstub rpm '
case "$1" in
-q) [ -f "${NFT_SB}/rpmdb" ] || exit 1; exit 0;;
esac
exit 1'
"${NFI}" -c "${SB}/test.cfg" -x; rc=$?
assert_rc    "reset rpm -e fails: rc" 1 "$rc"
assert_match "reset rpm -e fails: FATAL" ' FATAL ' "$(rtrace)"
assert_eq    "reset rpm -e fails: /usr/cnc kept" "${SB}/usr4/inc5.3.002" "$(readlink "${SB}/usr/cnc")"
assert_eq    "reset rpm -e fails: load dir kept" yes "$(exists "${SB}/usr4/inc5.3.002")"
assert_eq    "reset rpm -e fails: state" ispostinstall "$(state)"

# --- I2: leftover /usr/cnc_stage refuses a fresh install (R1) ---------------
setup
mkdir -p "${SB}/usr/cnc_stage/keep"
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "leftover stage dir: rc" 1 "$rc"
assert_match "leftover stage dir: FATAL" ' FATAL .*cnc_stage' "$(trace)"
assert_eq    "leftover stage dir: no dnf" 0 "$(count_of dnf)"
assert_eq    "leftover stage dir: no state" no "$(exists "${SB}/logs/.h1_INSTALL_STATE")"
assert_eq    "leftover stage dir: no info" no "$(exists "${SB}/logs/.h1_INSTALL_INFO")"
assert_eq    "leftover stage dir: kept" yes "$(exists "${SB}/usr/cnc_stage/keep")"
assert_eq    "leftover stage dir: no /usr/cnc" no "$(exists "${SB}/usr/cnc")"

setup
ln -s "${SB}/nowhere" "${SB}/usr/cnc_stage"
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "leftover stage link: rc" 1 "$rc"
assert_eq    "leftover stage link: no state" no "$(exists "${SB}/logs/.h1_INSTALL_STATE")"
assert_eq    "leftover stage link: no info" no "$(exists "${SB}/logs/.h1_INSTALL_INFO")"
assert_eq    "leftover stage link: kept" "${SB}/nowhere" "$(readlink "${SB}/usr/cnc_stage")"

# --- I3: unreadable DEPOTFILE / CNCCNFG refuse a fresh install (R1) --------
setup
sed "s|^DEPOTFILE=.*|DEPOTFILE=${SB}/missing.rpm|" "${SB}/test.cfg" > "${SB}/t2.cfg"
"${NFI}" -c "${SB}/t2.cfg" -i; rc=$?
assert_rc    "unreadable depot: rc" 1 "$rc"
assert_match "unreadable depot: FATAL" ' FATAL .*DEPOTFILE' "$(trace)"
assert_eq    "unreadable depot: no state" no "$(exists "${SB}/logs/.h1_INSTALL_STATE")"
assert_eq    "unreadable depot: no info" no "$(exists "${SB}/logs/.h1_INSTALL_INFO")"
assert_eq    "unreadable depot: no /usr/cnc" no "$(exists "${SB}/usr/cnc")"

setup
sed "s|^CNCCNFG=.*|CNCCNFG=${SB}/missing.cnfg|" "${SB}/test.cfg" > "${SB}/t2.cfg"
"${NFI}" -c "${SB}/t2.cfg" -i; rc=$?
assert_rc    "unreadable cnc.cnfg: rc" 1 "$rc"
assert_match "unreadable cnc.cnfg: FATAL" ' FATAL .*CNCCNFG' "$(trace)"
assert_eq    "unreadable cnc.cnfg: no state" no "$(exists "${SB}/logs/.h1_INSTALL_STATE")"
assert_eq    "unreadable cnc.cnfg: no info" no "$(exists "${SB}/logs/.h1_INSTALL_INFO")"
assert_eq    "unreadable cnc.cnfg: no dnf" 0 "$(count_of dnf)"

# --- I1: resume refuses when /usr/cnc is not our link ----------------------
# mkfake <dir> — a post-install.sh / cnc.cnfg target under <dir> that logs if run.
mkfake() {
	mkdir -p "$1/bin" "$1/features"
	printf '#!/bin/sh\necho "post-install.sh $*" >> "%s/calls"\nexit 0\n' "${SB}" > "$1/bin/post-install.sh"
	chmod +x "$1/bin/post-install.sh"
}
setup
mkfake "${SB}/usr/cnc"
print ispostinstall > "${SB}/logs/.h1_INSTALL_STATE"
printf 'LOADDIR=%s\nLOADDIR_CREATED=yes\n' "${SB}/usr4/inc5.3.002" > "${SB}/logs/.h1_INSTALL_INFO"
"${NFI}" -c "${SB}/test.cfg" -r; rc=$?
assert_rc    "resume /usr/cnc real dir: rc" 1 "$rc"
assert_match "resume /usr/cnc real dir: FATAL" ' FATAL .*usr/cnc' "$(trace)"
assert_eq    "resume /usr/cnc real dir: nothing run" 0 "$(count_of post-install.sh)"
assert_eq    "resume /usr/cnc real dir: state" ispostinstall "$(state)"
assert_eq    "resume /usr/cnc real dir: dir kept" yes "$(exists "${SB}/usr/cnc/bin/post-install.sh")"

setup
mkfake "${SB}/usr4/other"
mkdir -p "${SB}/usr4/inc5.3.002"
ln -s "${SB}/usr4/other" "${SB}/usr/cnc"
print ispostinstall > "${SB}/logs/.h1_INSTALL_STATE"
printf 'LOADDIR=%s\nLOADDIR_CREATED=yes\n' "${SB}/usr4/inc5.3.002" > "${SB}/logs/.h1_INSTALL_INFO"
"${NFI}" -c "${SB}/test.cfg" -r; rc=$?
assert_rc    "resume /usr/cnc foreign link: rc" 1 "$rc"
assert_eq    "resume /usr/cnc foreign link: nothing run" 0 "$(count_of post-install.sh)"
assert_eq    "resume /usr/cnc foreign link: state" ispostinstall "$(state)"
assert_eq    "resume /usr/cnc foreign link: link kept" "${SB}/usr4/other" "$(readlink "${SB}/usr/cnc")"

setup
mkdir -p "${SB}/usr4/inc5.3.002"
print isrpm > "${SB}/logs/.h1_INSTALL_STATE"
printf 'LOADDIR=%s\nLOADDIR_CREATED=yes\n' "${SB}/usr4/inc5.3.002" > "${SB}/logs/.h1_INSTALL_INFO"
"${NFI}" -c "${SB}/test.cfg" -r; rc=$?
assert_rc    "resume /usr/cnc absent: rc" 1 "$rc"
assert_eq    "resume /usr/cnc absent: no dnf" 0 "$(count_of dnf)"
assert_eq    "resume /usr/cnc absent: state" isrpm "$(state)"

# --- I1: resume past isunstage refuses a foreign /usr/cnc_stage ------------
setup
FAIL_POSTINSTALL=1 "${NFI}" -c "${SB}/test.cfg" -i; rc=$?
mkdir -p "${SB}/usr4/other"
ln -s "${SB}/usr4/other" "${SB}/usr/cnc_stage"
"${NFI}" -c "${SB}/test.cfg" -r; rc=$?
assert_rc    "resume foreign stage: rc" 1 "$rc"
assert_eq    "resume foreign stage: post-install once" 1 "$(count_of post-install.sh)"
assert_eq    "resume foreign stage: state" ispostinstall "$(state)"
assert_eq    "resume foreign stage: link kept" "${SB}/usr4/other" "$(readlink "${SB}/usr/cnc_stage")"

setup
FAIL_POSTINSTALL=1 "${NFI}" -c "${SB}/test.cfg" -i; rc=$?
mkdir -p "${SB}/usr/cnc_stage/keep"
"${NFI}" -c "${SB}/test.cfg" -r; rc=$?
assert_rc    "resume real stage dir: rc" 1 "$rc"
assert_eq    "resume real stage dir: post-install once" 1 "$(count_of post-install.sh)"
assert_eq    "resume real stage dir: kept" yes "$(exists "${SB}/usr/cnc_stage/keep")"

# --- F3: -r refuses when the config's LOADDIR differs from the recorded (R3) -
setup
FAIL_POSTINSTALL=1 "${NFI}" -c "${SB}/test.cfg" -i; rc=$?
sed 's/^NEW_LOAD=.*/NEW_LOAD=03/' "${SB}/test.cfg" > "${SB}/t2.cfg"
"${NFI}" -c "${SB}/t2.cfg" -r; rc=$?
assert_rc    "resume LOADDIR mismatch: rc" 1 "$rc"
assert_match "resume LOADDIR mismatch: FATAL" ' FATAL .*use reset' "$(trace)"
assert_eq    "resume LOADDIR mismatch: post-install once" 1 "$(count_of post-install.sh)"
assert_eq    "resume LOADDIR mismatch: state" ispostinstall "$(state)"
assert_eq    "resume LOADDIR mismatch: no new load dir" no "$(exists "${SB}/usr4/inc5.3.003")"

# --- F3: isprepare refuses a pre-existing real stage dir / foreign link ----
setup
print isprepare > "${SB}/logs/.h1_INSTALL_STATE"
printf 'LOADDIR=%s\n' "${SB}/usr4/inc5.3.002" > "${SB}/logs/.h1_INSTALL_INFO"
mkdir -p "${SB}/usr/cnc_stage/keep"
"${NFI}" -c "${SB}/test.cfg" -r; rc=$?
assert_rc    "isprepare real stage dir: rc" 1 "$rc"
assert_match "isprepare real stage dir: FATAL" ' FATAL .*cnc_stage exists' "$(trace)"
assert_eq    "isprepare real stage dir: kept" yes "$(exists "${SB}/usr/cnc_stage/keep")"
assert_eq    "isprepare real stage dir: state" isprepare "$(state)"
assert_eq    "isprepare real stage dir: no dnf" 0 "$(count_of dnf)"

setup
print isprepare > "${SB}/logs/.h1_INSTALL_STATE"
printf 'LOADDIR=%s\n' "${SB}/usr4/inc5.3.002" > "${SB}/logs/.h1_INSTALL_INFO"
mkdir -p "${SB}/usr4/other"
ln -s "${SB}/usr4/other" "${SB}/usr/cnc_stage"
"${NFI}" -c "${SB}/test.cfg" -r; rc=$?
assert_rc    "isprepare foreign stage link: rc" 1 "$rc"
assert_match "isprepare foreign stage link: FATAL" ' FATAL .*cnc_stage is a link to' "$(trace)"
assert_eq    "isprepare foreign stage link: kept" "${SB}/usr4/other" "$(readlink "${SB}/usr/cnc_stage")"
assert_eq    "isprepare foreign stage link: state" isprepare "$(state)"
assert_eq    "isprepare foreign stage link: no dnf" 0 "$(count_of dnf)"

# --- F3/M7: isunstage refuses a real dir and a foreign link ----------------
setup
STAGE_REAL=1 "${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "isunstage real dir: rc" 1 "$rc"
assert_match "isunstage real dir: FATAL" ' FATAL .*cnc_stage' "$(trace)"
assert_eq    "isunstage real dir: kept" yes "$(exists "${SB}/usr/cnc_stage/keep")"
assert_eq    "isunstage real dir: state" isunstage "$(state)"
assert_eq    "isunstage real dir: no linux_packages" 0 "$(count_of linux_packages)"

setup
STAGE_FOREIGN=1 "${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "isunstage foreign link: rc" 1 "$rc"
assert_match "isunstage foreign link: FATAL" ' FATAL .*cnc_stage is a link to' "$(trace)"
assert_eq    "isunstage foreign link: kept" "${SB}/usr4/other" "$(readlink "${SB}/usr/cnc_stage")"
assert_eq    "isunstage foreign link: state" isunstage "$(state)"
assert_eq    "isunstage foreign link: no linux_packages" 0 "$(count_of linux_packages)"

# --- backgrounding: without NFI_FOREGROUND it returns 0 at once --------------
setup
unset NFI_FOREGROUND
mkstub mountpoint 'exit 1'
"${NFI}" -c "${SB}/test.cfg" -i; rc=$?
assert_rc    "background: parent rc" 0 "$rc"
# Wait for the child to FINISH (its last line is "Aborting install"), not just
# its first FATAL, so the next setup does not rm -rf the sandbox under it (R4).
i=0
while [ $i -lt 100 ] && [ -z "$(trace | grep 'Aborting install')" ]; do sleep 0.1; i=$((i+1)); done
assert_match "background: child ran" ' FATAL ' "$(trace)"
assert_match "background: child finished" 'Aborting install' "$(trace)"

# --- usage -------------------------------------------------------------------
setup
"${NFI}" -c "${SB}/test.cfg" >/dev/null 2>&1; rc=$?
assert_rc    "usage: no action" 1 "$rc"
"${NFI}" -c "${SB}/test.cfg" -i -r >/dev/null 2>&1; rc=$?
assert_rc    "usage: two actions" 1 "$rc"

cleanup
nf_test_summary
