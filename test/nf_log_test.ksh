# nf_log_test.ksh
DIR="$(cd "$(dirname "$0")/.." && pwd)"
. "${DIR}/test/nf_test_lib.ksh"
. "${DIR}/nf_log.ksh"

TS='[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}'

assert_match "info format"  "^${TS} INFO  hello world$"  "$(loginfo 'hello world')"
assert_match "warn format"  "^${TS} WARN  careful$"      "$(logwarn 'careful')"
assert_match "error format" "^${TS} ERROR broke$"        "$(logerror 'broke')"
assert_match "fatal format" "^${TS} FATAL dead$"         "$(logfatal 'dead')"

export INCLOGDIR="${TMPDIR:-/tmp}"

# EXEC tagging: each child line is prefixed with timestamp, EXEC, and [tag].
out="$(run_logged demo printf 'one\ntwo\n')"
assert_match "exec line 1" "^${TS} EXEC  \[demo\] one$" "$(print -r -- "$out" | sed -n 1p)"
assert_match "exec line 2" "^${TS} EXEC  \[demo\] two$" "$(print -r -- "$out" | sed -n 2p)"

# Exit status of the child is preserved through the wrapper.
run_logged fail sh -c 'exit 7' >/dev/null
assert_rc "rc preserved" 7 "$?"

# A non-zero child also emits a WARN line naming the tag and code.
warn="$(run_logged fail sh -c 'exit 7')"
assert_match "nonzero warns" "WARN  \[fail\] exited 7" "$warn"

# A zero-exit child emits no WARN line.
ok="$(run_logged good sh -c 'exit 0')"
assert_match "zero no warn" '^$' "$(print -r -- "$ok" | grep WARN)"

nf_test_summary
