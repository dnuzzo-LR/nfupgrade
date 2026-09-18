# nf_log.ksh — logging helpers for nf_upgrade / nf_upgrader.
# Sourced, never executed. ksh88-safe (no PIPESTATUS/pipefail, no arrays).
# run_logged needs a writable dir; it uses ${INCLOGDIR:-/tmp}.

nf_log() {   # nf_log <SEVERITY> <message>
	printf '%s %-5s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" "$2"
}
loginfo()  { nf_log INFO  "$*"; }
logwarn()  { nf_log WARN  "$*"; }
logerror() { nf_log ERROR "$*"; }
logfatal() { nf_log FATAL "$*"; }

run_logged() {   # run_logged <tag> cmd [args...]
	typeset tag="$1"; shift
	typeset tmp="${INCLOGDIR:-/tmp}/.nfrun.$$"
	"$@" > "${tmp}" 2>&1
	typeset rc=$?
	sed "s/^/$(date '+%Y-%m-%d %H:%M:%S') EXEC  [${tag}] /" "${tmp}"
	rm -f "${tmp}"
	[ ${rc} -ne 0 ] && logwarn "[${tag}] exited ${rc}"
	return ${rc}
}
