# nfupgrader

Customer-facing web tool that wraps the netFLEX upgrade scripts so a customer can
upgrade the host they are logged into, with a consistent severity-prefixed log and
a read-only status view of every host in the site.

Design and plans live in `~/WorkNotes`:
- Design: `~/WorkNotes/design/2026-09-15-web-upgrade-tool-design.md`
- Phase 1 plan: `~/WorkNotes/plans/2026-09-17-nfupgrader-phase1-fork-logging-plan.md`

## Layout

| Path | What |
|------|------|
| `nf_upgrade`, `nf_upgrader` | Forks of netFLEX `inc_upgrade` / `inc_upgrader`, behavior-identical to the originals but with a cleaned-up, severity-prefixed log. Intended to eventually replace the originals in the product. |
| `nf_log.ksh` | Sourced logging helpers: `loginfo` / `logwarn` / `logerror` / `logfatal` and the status-preserving `run_logged` sub-command wrapper. |
| `ref/inc_upgrade`, `ref/inc_upgrader` | Pinned snapshots of the originals (netFLEX commit `776f42242`), used only as the baseline for the equivalence test. Not shipped. |
| `test/` | ksh unit tests and the fork verifier. |

## Constraints

- Shell is ksh, written **ksh88-safe** (`/usr/bin/ksh` on customer boxes). No `PIPESTATUS`, no `set -o pipefail`, no arrays in the shipped scripts.
- The Python service (later phases) targets **Python 3.6.8**, stdlib only — no third-party packages, no build step.
- No nmake here — this repo is standalone; ignore any netFLEX `BASE`/`VPATH` build environment.

## Testing

```sh
ksh test/nf_log_test.ksh      # unit tests for nf_log.ksh
sh  test/nf_fork_verify.sh    # syntax + behavioral-equivalence + coverage + grammar
```

`nf_fork_verify.sh` proves the forks are behaviorally identical to the pinned
originals in `ref/` (skeleton diff), that every script emit was converted
(coverage), and that converted/emitted lines match the log grammar.
