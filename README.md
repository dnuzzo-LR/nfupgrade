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

## Running the service on a host

Copy the whole repo to the host (keep the layout — the `nfupgrader/` package and
the `nf_upgrade` / `nf_upgrader` / `nf_log.ksh` scripts must stay siblings), then:

```sh
cd /opt/nfupgrader && ./run.sh
```

`run.sh` cd's to its own directory, so it can be invoked from anywhere. It prints
a single-use `/auth?token=…` URL. Run it as root on the host being upgraded.

### Environment variables

| Var | Default | Purpose |
|-----|---------|---------|
| `NFU_BIND` | `127.0.0.1` | Bind address. Set to `0.0.0.0` (or an interface IP) to reach the UI by hostname/ip without an ssh tunnel. |
| `NFU_URL_HOST` | hostname (when `NFU_BIND` is wildcard) | Host printed in the access URL, if the DNS name differs from `hostname`. |
| `NFU_PORT` | ephemeral | TCP port. |
| `INCLOGDIR` | `/usr/cnc/logs` | Where the state file and trace live. |
| `NFU_PHASE` | `stage` | `stage` or `upgrade` — which phase the progress record tracks. |
| `NFU_TRACEFILE` | `${INCLOGDIR}/${NFU_PHASE}_inc_$(hostname)` | Trace file the log viewer tails. |
| `NFU_USER_CONFIG` | (unset) | Customer's upgrade `.cfg`. On Launch, an effective copy is generated that sources it and forces `PROMPTFORVALIDATE=NO`; the original is never edited. Required to use the Launch buttons. |
| `NFU_EFFECTIVE_CONFIG` | `${INCLOGDIR}/nfupgrader_effective.cfg` | Where the generated effective config is written and read. |
| `NFU_LAUNCH_CWD` | dir of `NFU_USER_CONFIG` | Directory `nf_upgrade` runs from (for relative `DEPOTFILE`/`linux_packages` lookup). |

**Loopback (default):** print shows an `ssh -L` tunnel hint. **Non-loopback:** the
URL uses the hostname and a plaintext-HTTP warning is printed (token/URL cross the
network unencrypted). The session cookie is bound to the client IP.

**Launch is guarded** by a browser confirm dialog, but there is no other gate — a
confirmed click runs the real upgrade immediately. Only click on a host prepared
for it. Just running the service launches nothing; it only observes.

### Example — reach it by hostname on a lab box

```sh
export NFU_BIND=0.0.0.0 NFU_PORT=8099
export INCLOGDIR=/usr/cnc/logs NFU_PHASE=stage
export NFU_TRACEFILE=/usr/cnc/logs/stage_inc_$(hostname)
export NFU_USER_CONFIG=/opt/apt/upgrade/my.cfg   # only needed to Launch
./run.sh    # browse the printed http://<host>:8099/auth?token=… URL
```

## Testing

```sh
ksh test/nf_log_test.ksh                          # unit tests for nf_log.ksh
sh  test/nf_fork_verify.sh                         # fork syntax/equivalence/coverage/grammar
python3 -m unittest discover -s tests -p 'test_*.py'   # Phase 2 python tests
```

`nf_fork_verify.sh` proves the forks are behaviorally identical to the pinned
originals in `ref/` (skeleton diff), that every script emit was converted
(coverage), and that converted/emitted lines match the log grammar.
