#!/bin/bash
#
# nf_uninstall.sh — remove an installed netFLEX from THIS box, so a fresh install
# can be tested again. LAB/TEST USE ONLY: this destroys netFLEX and its data.
#
# Usage: nf_uninstall.sh [--dry-run] [--purge-db] [--logdir DIR]
#   --dry-run   list what would be removed, change nothing (no root needed)
#   --purge-db  also stop/disable postgresql and empty /var/lib/pgsql/data
#               (the database nf_upgrade_pg.sh initialised; the postgresql
#               packages themselves are kept)
#   --logdir    nfupgrader's log dir, whose .<host>_INSTALL_STATE/_INFO files are
#               archived so the tool starts clean (default /opt/nfupgrader/logs)
#
# Removes, in order: netFLEX systemd units, the netFLEX* RPMs, the /usr/cnc,
# /usr/cnc_stage and /usr/cnc_saved links, the /usr4/inc* load directories, and
# the users/group the CORE RPM creates, then nf_install's Apache grant file. Refuses while
# the app is up or nf_install / nf_upgrader / nf_patch runs. Always asks for the host name.

# (No `set -u`: bash 4.2 on RHEL 7 treats "${empty[@]}" as unbound.)

PURGE_DB=no
DRY_RUN=no
LOGDIR=/opt/nfupgrader/logs
LOADROOT=/usr4
LINKS="/usr/cnc /usr/cnc_stage /usr/cnc_saved"
USERS="lrs_support console cpymem ss_user tftp cmm"   # cmm last: others share its uid
UNITS="netFLEX netFLEX-md netFLEX-md_cnc netFLEX-mux_in netFLEX-mux_out inc-ts web-session-gc"
PGDATA=/var/lib/pgsql/data
WEBACCESS_CONF=/etc/httpd/conf.d/nfupgrader-webgui.conf   # nf_install's Apache grant
WEBACCESS_MARK="# Written by nf_install (nfupgrader)."

while [ $# -gt 0 ]; do
	case "$1" in
	--dry-run)  DRY_RUN=yes ;;
	--purge-db) PURGE_DB=yes ;;
	--logdir)   shift; LOGDIR="${1:-}" ;;
	-h|--help)  sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
	*)          echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
	esac
	shift
done

RED=$'\e[1;37;41m'; YEL=$'\e[1;33m'; OFF=$'\e[0m'
[ -t 1 ] || { RED=""; YEL=""; OFF=""; }
HOST=$(hostname)

die() { echo "${YEL}nf_uninstall: $*${OFF}" >&2; exit 1; }

# --- inventory ---------------------------------------------------------------
mapfile -t PKGS < <(rpm -qa 'netFLEX*' 2>/dev/null | sort)
LINKS_FOUND=()
for l in ${LINKS}; do
	if [ -L "$l" ] || [ -e "$l" ]; then LINKS_FOUND+=("$l"); fi
done
LOADDIRS=()
for d in "${LOADROOT}"/inc[0-9]*; do
	[ -d "$d" ] && [ ! -L "$d" ] && LOADDIRS+=("$d")
done
UNIT_FILES=()
for u in ${UNITS}; do
	for f in /etc/systemd/system/"$u".service /etc/systemd/system/"$u".timer \
	         /usr/lib/systemd/system/"$u".service /usr/lib/systemd/system/"$u".timer; do
		[ -e "$f" ] && UNIT_FILES+=("$f")
	done
done
USERS_FOUND=()
for u in ${USERS}; do getent passwd "$u" >/dev/null && USERS_FOUND+=("$u"); done
CONF_FILES=()
if [ -f "${WEBACCESS_CONF}" ] && grep -qxF "${WEBACCESS_MARK}" "${WEBACCESS_CONF}"; then
	CONF_FILES+=("${WEBACCESS_CONF}")
fi
STATE_FILES=()
if [ -d "${LOGDIR}" ]; then
	for f in "${LOGDIR}"/.*_INSTALL_STATE "${LOGDIR}"/.*_INSTALL_INFO; do
		[ -f "$f" ] && STATE_FILES+=("$f")
	done
fi

show() {   # show <title> <items...>
	local title="$1"; shift
	if [ $# -eq 0 ]; then printf '  %-28s (none)\n' "$title"; return; fi
	printf '  %s\n' "$title"
	printf '      %s\n' "$@"
}

echo
echo "netFLEX found on ${HOST}:"
show "RPMs (rpm -e):"                    "${PKGS[@]}"
show "links (/usr/cnc*):"               "${LINKS_FOUND[@]}"
show "load dirs (rm -rf):"               "${LOADDIRS[@]}"
show "systemd units (stop, disable, rm):" "${UNIT_FILES[@]}"
show "users (userdel), + group cmm:"     "${USERS_FOUND[@]}"
show "nf_install Apache grant (rm):"      "${CONF_FILES[@]}"
if [ "${PURGE_DB}" = yes ]; then
	show "postgres data (emptied):" "${PGDATA}"
else
	echo "  postgres data                kept (use --purge-db to empty ${PGDATA})"
fi
show "nfupgrader install state (archived):" "${STATE_FILES[@]}"
echo

if [ ${#PKGS[@]} -eq 0 ] && [ ${#LINKS_FOUND[@]} -eq 0 ] && [ ${#LOADDIRS[@]} -eq 0 ] \
   && [ ${#UNIT_FILES[@]} -eq 0 ] && [ ${#USERS_FOUND[@]} -eq 0 ] && [ ${#CONF_FILES[@]} -eq 0 ] \
   && [ "${PURGE_DB}" = no ]; then
	echo "Nothing to remove - netFLEX is not installed here."
	exit 0
fi
[ "${DRY_RUN}" = yes ] && { echo "Dry run: nothing was changed."; exit 0; }

# --- refusals ------------------------------------------------------------------
[ "$(id -u)" -eq 0 ] || die "must be run as root"
[ -e /usr/cnc/.CNC_UP ] && die "netFLEX is UP. Shut it down first (as cmm: /usr/cnc/bin/new_boot_nms shutdown)."
if pgrep -f 'nf_install|nf_upgrader|inc_upgrader|nf_patch' >/dev/null 2>&1; then
	die "an install, upgrade or patch is running (nf_install/nf_upgrader/nf_patch). Wait for it to finish."
fi

# --- banner + prompt -----------------------------------------------------------
if [ "${PURGE_DB}" = yes ]; then
	DBLINE="   The postgres database in ${PGDATA} is ERASED too.                 "
else
	DBLINE="   (The postgres database is kept; --purge-db erases it.)            "
fi
cat <<EOF

${RED}                                                                          ${OFF}
${RED}   !!!  WARNING  -  THIS PERMANENTLY DESTROYS netFLEX ON ${HOST}   ${OFF}
${RED}                                                                          ${OFF}
${RED}   Everything listed above is removed: the software, the load           ${OFF}
${RED}   directories under ${LOADROOT} and ALL netFLEX data and configuration   ${OFF}
${RED}   in them.                                                               ${OFF}
${RED}${DBLINE}     ${OFF}
${RED}   There is no undo. Only run this on a LAB / TEST box.                   ${OFF}
${RED}                                                                          ${OFF}

EOF
printf 'To continue, type this host name exactly (%s): ' "${HOST}"
read -r answer
[ "${answer}" = "${HOST}" ] || die "host name did not match - nothing was changed."
printf 'Last chance. Type YES to remove netFLEX: '
read -r answer
[ "${answer}" = "YES" ] || die "not confirmed - nothing was changed."

LOG="/root/nf_uninstall.$(date +%Y%m%d%H%M%S).log"
exec > >(tee -a "${LOG}") 2>&1
echo "nf_uninstall on ${HOST} started $(date) - log ${LOG}"
FAILED=0
run() {   # run <command...>: echo, run, note failures, keep going
	echo "+ $*"
	"$@" || { echo "${YEL}  ^ failed (rc $?)${OFF}"; FAILED=$((FAILED + 1)); }
}

# --- 1. systemd units -----------------------------------------------------------
for u in ${UNITS}; do
	for kind in service timer; do
		systemctl list-unit-files "$u.$kind" >/dev/null 2>&1 || continue
		systemctl stop "$u.$kind" >/dev/null 2>&1
		systemctl disable "$u.$kind" >/dev/null 2>&1
	done
done
for f in "${UNIT_FILES[@]}"; do run rm -f "$f"; done
rm -f /etc/systemd/system/multi-user.target.wants/netFLEX*.service
run systemctl daemon-reload

# --- 2. RPMs (scripts first; fall back to --noscripts if a scriptlet fails) ----
for p in "${PKGS[@]}"; do
	echo "+ rpm -e ${p}"
	if ! rpm -e "${p}"; then
		echo "${YEL}  rpm -e ${p} failed - retrying with --noscripts${OFF}"
		run rpm -e --noscripts "${p}"
	fi
done

# --- 3. links (and a real /usr/cnc dir, if one was left behind) ---------------
for l in "${LINKS_FOUND[@]}"; do
	if [ -L "$l" ]; then run rm -f "$l"; else run rm -rf "$l"; fi
done

# --- 4. load directories ---------------------------------------------------------
for d in "${LOADDIRS[@]}"; do
	case "$d" in "${LOADROOT}"/inc[0-9]*) run rm -rf "$d" ;; esac
done

# --- 5. users and group ------------------------------------------------------------
for u in "${USERS_FOUND[@]}"; do
	pkill -u "$u" >/dev/null 2>&1
	run userdel "$u"
done
getent group cmm >/dev/null && run groupdel cmm

# --- 6. postgres data (optional) ---------------------------------------------------
if [ "${PURGE_DB}" = yes ]; then
	systemctl stop postgresql >/dev/null 2>&1
	systemctl disable postgresql >/dev/null 2>&1
	if [ -d "${PGDATA}" ]; then
		run find "${PGDATA}" -mindepth 1 -delete
	fi
fi

# --- 6b. nf_install's Apache grant (it points at the /usr/cnc just removed) -----
for f in "${CONF_FILES[@]}"; do run rm -f "$f"; done

# --- 7. nfupgrader install state ------------------------------------------------------
TS=$(date +%Y%m%d%H%M%S)
for f in "${STATE_FILES[@]}"; do run mv "$f" "$f.$TS"; done

echo
left=$(rpm -qa 'netFLEX*')
if [ -n "${left}" ] || [ -e /usr/cnc ] || [ -L /usr/cnc ]; then
	echo "${YEL}netFLEX is NOT fully removed:${OFF} ${left} $(ls -ld /usr/cnc 2>/dev/null)"
	exit 1
fi
if [ "${FAILED}" -gt 0 ]; then
	echo "${YEL}netFLEX removed, but ${FAILED} step(s) failed - see ${LOG}${OFF}"
	exit 1
fi
echo "netFLEX removed from ${HOST}. Log: ${LOG}"
