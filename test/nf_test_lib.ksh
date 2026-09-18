# nf_test_lib.ksh — minimal ksh assertion helpers. Source this, call asserts,
# then call nf_test_summary at the end. Exit code reflects failures.
integer NF_TEST_PASS=0
integer NF_TEST_FAIL=0

assert_eq() {   # assert_eq <label> <expected> <actual>
	if [ "$2" = "$3" ]; then
		NF_TEST_PASS=$((NF_TEST_PASS+1)); printf 'ok   %s\n' "$1"
	else
		NF_TEST_FAIL=$((NF_TEST_FAIL+1))
		printf 'FAIL %s\n     expected: [%s]\n     actual:   [%s]\n' "$1" "$2" "$3"
	fi
}

assert_match() {   # assert_match <label> <ere> <actual>
	if print -r -- "$3" | grep -qE "$2"; then
		NF_TEST_PASS=$((NF_TEST_PASS+1)); printf 'ok   %s\n' "$1"
	else
		NF_TEST_FAIL=$((NF_TEST_FAIL+1))
		printf 'FAIL %s\n     pattern: [%s]\n     actual:  [%s]\n' "$1" "$2" "$3"
	fi
}

assert_rc() {   # assert_rc <label> <expected_rc> <actual_rc>
	assert_eq "$1" "$2" "$3"
}

nf_test_summary() {
	printf -- '---- %d passed, %d failed ----\n' "$NF_TEST_PASS" "$NF_TEST_FAIL"
	[ "$NF_TEST_FAIL" -eq 0 ]
}
