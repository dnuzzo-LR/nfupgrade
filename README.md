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
| `nf_install` | New (not a fork): fresh netFLEX CORE install of the local host, with resume and reset. See *Installing on a new host*. |
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

`run.sh` cd's to its own directory, so it can be invoked from anywhere; the
directory you run it from is remembered as the default RPM depot (see
`NFU_DEPOT_DIRS`). It prints a single-use `/auth?token=…` URL. Run it as root on
the host being upgraded.

### Environment variables

| Var | Default | Purpose |
|-----|---------|---------|
| `NFU_BIND` | `127.0.0.1` | Bind address. Set to `0.0.0.0` (or an interface IP) to reach the UI by hostname/ip without an ssh tunnel. |
| `NFU_URL_HOST` | hostname (when `NFU_BIND` is wildcard) | Host printed in the access URL, if the DNS name differs from `hostname`. |
| `NFU_PORT` | ephemeral | TCP port. |
| `INCLOGDIR` | `<dir run.sh was started from>/logs` (created if missing) | Where nfupgrader keeps its own files (audit log, saved/effective config, report snapshots). The upgrade's state file and traces are read from the `INCLOGDIR` set in the active upgrade config (Config tab save, else `NFU_USER_CONFIG`), falling back to this. Overview shows both when they differ. The Config tab pre-fills the upgrade config's `INCLOGDIR` with this directory, so by default both are the same (a fixed place outside the netFLEX load). |
| `NFU_PHASE` | (unset: inferred) | `stage` or `upgrade` — which phase the progress strip and Log tab follow before anything is launched. Once you Launch, the mode you launched takes over. Unset, it is inferred from the state file (`swi*`/`postinstall` → upgrade, otherwise stage). |
| `NFU_TRACEFILE` | newest `<upgrade log dir>/${phase}_inc_$(hostname).<pid>` (the upgrade log dir is `INCLOGDIR` from the active upgrade config) | Trace file the Log tab tails. Unset (recommended), the newest trace nf_upgrader wrote for the current phase is followed, switching automatically on a new run. |
| `NFU_USER_CONFIG` | (unset) | Customer's upgrade `.cfg`. On Launch, an effective copy is generated that sources it and forces `PROMPTFORVALIDATE=NO`; the original is never edited. Required to use the Launch buttons. |
| `NFU_EFFECTIVE_CONFIG` | `${INCLOGDIR}/nfupgrader_effective.cfg` | Where the generated effective config is written and read. |
| `NFU_LAUNCH_CWD` | dir of `NFU_USER_CONFIG` | Directory `nf_upgrade` runs from (for relative `DEPOTFILE`/`linux_packages` lookup). |
| `NFU_DEPOT_DIRS` | directory `run.sh` was started from, then `/opt/apt/upgrade` | Colon-separated directories scanned (top level only) for `netFLEX-CORE*.rpm`. The Config tab lists them automatically on page load; **Refresh depots** re-scans. Picking one fills `DEPOTFILE`, `NEW_GENERIC` and `NEW_LOAD` from the filename. Missing directories are skipped. |
| `NFU_PREVIEW_INSTALL` | (unset: off) | Preview the install screens on any box, even one already running netFLEX. Set to `1` (or `before`, `running`, `failed`, `done`). The UI shows install mode with sample data and a yellow PREVIEW banner whose picker switches between those scenarios. Pre-flight checks run for real (read-only). **Launch, Resume, Reset and Fix are refused**; Save writes `nfupgrader_preview.cfg` / `nfupgrader_preview_cnc.cnfg` beside — never over — the real saved config. |
| `NFU_REPORTS` | `nfupgrader/reports.default.json` | Report list for the Reports tab. Snapshots go to `${INCLOGDIR}/nfupgrader_reports/` (newest 20 kept); an Upgrade launch takes a *before* snapshot first and an *after* snapshot when the upgrade ends. |

**Loopback (default):** print shows an `ssh -L` tunnel hint. **Non-loopback:** the
URL uses the hostname and a plaintext-HTTP warning is printed (token/URL cross the
network unencrypted). The session cookie is bound to the client IP.

**Launch is guarded** by a browser confirm dialog, but there is no other gate — a
confirmed click runs the real upgrade immediately. Only click on a host prepared
for it. Just running the service launches nothing; it only observes.

### Example — reach it by hostname on a lab box

```sh
export NFU_BIND=0.0.0.0 NFU_PORT=8099
export NFU_USER_CONFIG=/opt/apt/upgrade/my.cfg   # only needed to Launch
./run.sh    # browse the printed http://<host>:8099/auth?token=… URL
```

## Installing on a new host

When netFLEX is not installed on the host (or an install is unfinished), the tool
runs in **install mode** (`GET /api/install/status` reports `"mode": "install"`):

- **Overview** says netFLEX is not installed and points to the Config tab; the site
  table is hidden.
- **Config** shows the install form: CORE RPM, box type (Singlebox/FEP/BEP), mate
  host and master for a FEP/BEP pair, web GUI, and a live `cnc.cnfg` preview.
  **Use this config** saves it.
- The **Upgrade** tab becomes the **Install** tab with a single *Install* phase. Run
  the pre-flight checks, then Launch. A *hard* check (e.g. "netFLEX already
  installed") can never be overridden; other blocking checks can, with a reason.
- Launch runs `nf_install` in the background; the progress strip shows the 13
  install steps and the Log tab follows the install trace.
- When it finishes, an **Install complete** banner keeps the progress, post-flight
  results and any WARN lines from the trace on screen. **Continue to upgrade view**
  (or a page reload) switches the page to the normal stage/upgrade mode.

### `nf_install`

`nf_install` is new (not a fork of a netFLEX script). It runs as root and re-runs
itself in the background unless `NFI_FOREGROUND=YES`.

```sh
nf_install -c <config file> -i     # fresh install (refused if unfinished, already installed,
                                   # /usr/cnc_stage left over, or DEPOTFILE/CNCCNFG unreadable)
nf_install -c <config file> -r     # resume an unfinished install at its recorded step
nf_install -c <config file> -x     # reset a failed install
nf_install -h                      # help
```

Files, all in the config's `INCLOGDIR`:

| File | What |
|------|------|
| `.<host>_INSTALL_STATE` | The step to run next. A fatal step leaves it at that step (the resume point); `isdone` when finished. Kept afterwards as a record. The install never writes `.<host>_STATE` (nf_upgrader's resume point). |
| `.<host>_INSTALL_INFO` | `KEY=VALUE`: `LOADDIR`, `VERSION`, `DEPOTFILE`, and `LOADDIR_CREATED=yes` once the install has created the load dir itself. |
| `install_inc_<host>.<pid>` / `reset_inc_<host>.<pid>` | Trace files (severity-prefixed log) for an install/resume and a reset. Never deleted. |

### When an install fails

The Install tab shows the failed step, the ERROR/FATAL lines from the trace and the
trace path to send to support, with two actions:

- **Resume install** re-runs the pre-flight checks (hard checks are skipped on a
  resume) and relaunches `nf_install -r`, which continues from the failed step.
  Past the prepare step, resume refuses (changing nothing) unless `/usr/cnc` is
  the install's link to the recorded load dir and `/usr/cnc_stage` is absent or
  that same link, so it never runs install steps over a netFLEX it did not create.
  Install and resume both need a saved install configuration (readable
  `DEPOTFILE` and `CNCCNFG`, a writable `INCLOGDIR` outside `/usr/cnc`).
- **Reset and start over** asks for a reason (audited) and runs `nf_install -x`. It
  removes only what this install provably created: the netFLEX-CORE RPM if
  installed, the `/usr/cnc` and `/usr/cnc_stage` links only if they point at the
  recorded load dir, and the load dir itself only if `LOADDIR_CREATED=yes` (otherwise
  it is left in place). Every refusal check runs first; if a link points elsewhere,
  or the recorded load dir is not a `/usr4/inc*` directory, nothing is removed. The
  state/info files are archived with a timestamp and the traces are kept. Reset is
  refused while anything is running.

### Removing netFLEX to re-test a fresh install (lab boxes only)

`tools/nf_uninstall.sh` removes an installed netFLEX from the box it runs on: the
`netFLEX*` RPMs, the `/usr/cnc*` links, the `/usr4/inc*` load directories, the
netFLEX systemd units and the users/group the CORE RPM creates (and, with
`--purge-db`, the postgres data in `/var/lib/pgsql/data`). Run `--dry-run` first to
see the list. A real run must be root, refuses while the app is up or an
install/upgrade is running, shows a warning banner, and asks you to type the host
name and then `YES`. It logs to `/root/nf_uninstall.<timestamp>.log`.

### Previewing the install screens

To see the install UI on a box that already has netFLEX (e.g. a lab box):

```sh
NFU_PREVIEW_INSTALL=1 ./run.sh      # plus your usual NFU_BIND/NFU_PORT
```

Use the picker in the yellow banner to switch between *Before install*, *Install
running*, *Install failed* (failure panel, Resume/Reset) and *Install complete*
(done banner). Nothing is installed, reset or fixed in preview mode.

## Testing

```sh
ksh test/nf_log_test.ksh                          # unit tests for nf_log.ksh
ksh test/nf_install_test.ksh                      # nf_install in a sandbox
sh  test/nf_fork_verify.sh                         # fork syntax/equivalence/coverage/grammar
python3 -m unittest discover -s tests -p 'test_*.py'   # Phase 2 python tests
```

`nf_fork_verify.sh` proves the forks are behaviorally identical to the pinned
originals in `ref/` (skeleton diff), that every script emit was converted
(coverage), and that converted/emitted lines match the log grammar.
