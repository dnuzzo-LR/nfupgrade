#!/bin/sh
# nf_fork_verify.sh — static equivalence/coverage/grammar checks for the forks.
# Run from repo root: sh 3b2/shell/test/nf_fork_verify.sh
cd "$(dirname "$0")/.." || exit 2   # -> 3b2/shell
fail=0

# --- 1. Syntax --------------------------------------------------------------
# (stderr suppressed: the originals use `...` backticks, which ksh -n warns
#  about but accepts; those warnings are inherited and not our concern.)
for f in nf_upgrade nf_upgrader nf_install; do
	if ksh -n "$f" 2>/dev/null; then echo "ok   syntax $f"
	else echo "FAIL syntax $f"; fail=1; fi
done

# --- 2. Behavioral skeleton: fork must equal original once emits/rename/wrap
#        are normalized away. Proves no control-flow line changed. -----------
# B() reduces a script to its behavioral skeleton:
#   * drop the sourced-include line
#   * rename nf_upgrade(r) -> inc_upgrade(r)  (neutralize the fork rename)
#   * unwrap "run_logged <tag> " prefixes back to the bare command
#   * delete every emit/log line that has NO "[space]>" redirection
#     (bare printf/print/echo AND log*/logfatal calls); keep redirected
#     emits (control-file writes) so they ARE compared.
B() {
	sed -e '/NF_LOG_LIB/d' \
	    -e 's/nf_upgrader/inc_upgrader/g' -e 's/nf_upgrade/inc_upgrade/g' \
	    -e 's/^\([[:space:]]*\)run_logged [^ ]* /\1/' "$1" \
	| awk '!(/^[[:space:]]*(printf|print|echo|log|loginfo|logwarn|logerror|logfatal)([[:space:]]|\(|$)/ && $0 !~ /[[:space:]]>/)'
}
# Originals are vendored under ref/ as pinned snapshots (netflex commit 776f42242).
for pair in "ref/inc_upgrade nf_upgrade" "ref/inc_upgrader nf_upgrader"; do
	set -- $pair
	B "$1" >/tmp/nfb.orig.$$
	B "$2" >/tmp/nfb.fork.$$
	if diff /tmp/nfb.orig.$$ /tmp/nfb.fork.$$ >/tmp/nfb.diff.$$ 2>&1; then
		echo "ok   skeleton $2 == $1"
	else
		echo "FAIL skeleton $2 != $1"; sed 's/^/       /' /tmp/nfb.diff.$$; fail=1
	fi
	rm -f /tmp/nfb.orig.$$ /tmp/nfb.fork.$$ /tmp/nfb.diff.$$
done

# --- 3. Emit coverage: no unconverted bare emit remains in a fork, except the
#        banner/usage allowlist. A bare emit = printf/print/echo at line start
#        with no "[space]>" redirection. ----------------------------------
check_coverage() {   # check_coverage <fork> <sed-range-deletions...>
	fork="$1"; shift
	remaining=$(sed "$@" "$fork" \
		| grep -nE '^[[:space:]]*(printf|print|echo)([[:space:]]|$)' \
		| grep -vE '[[:space:]]>')
	if [ -z "$remaining" ]; then echo "ok   coverage $fork"
	else echo "FAIL coverage $fork (unconverted bare emits):"; echo "$remaining" | sed 's/^/       /'; fail=1; fi
}
# Ranges matched by content, not line number, so they survive edits:
check_coverage nf_upgrader \
	-e '/^function tool_banner$/,/^}$/d' \
	-e '/^function print_usage$/,/^}$/d'
check_coverage nf_upgrade \
	-e '/^function print_usage$/,/^}$/d'

# --- 4. Grammar: every log*/logfatal call carries a quoted message. ---------
for f in nf_upgrade nf_upgrader nf_install; do
	bad=$(grep -nE '^[[:space:]]*(loginfo|logwarn|logerror|logfatal)([[:space:]]|$)' "$f" \
		| grep -vE '(loginfo|logwarn|logerror|logfatal)[[:space:]]+"' || true)
	if [ -z "$bad" ]; then echo "ok   grammar $f"
	else echo "FAIL grammar $f (log call without quoted message):"; echo "$bad" | sed 's/^/       /'; fail=1; fi
done

# --- 5. Runtime grammar: sourcing the include and emitting really produces
#        the TIMESTAMP SEVERITY grammar (catches a broken date/format). ------
line=$(ksh -c '. ./nf_log.ksh; loginfo "smoke test"')
if echo "$line" | grep -qE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} INFO  smoke test$'; then
	echo "ok   runtime-grammar"
else
	echo "FAIL runtime-grammar: [$line]"; fail=1
fi

echo "----"
[ "$fail" -eq 0 ] && echo "VERIFY OK" || echo "VERIFY FAILED"
exit $fail
