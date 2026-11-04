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

## Boot menu

The **Boot menu** tab runs netFLEX's boot menu (`/usr/cnc/bin/new_boot_nms`, as
user `cmm`, on a real terminal) in the browser, so the application can be booted
or shut down without a separate login. The terminal is xterm.js; it talks to the
tool over the `/ws/bootmenu` WebSocket (same login cookie as the rest of the UI).
There is never a shell prompt: when the menu exits, the session ends.

- **Availability.** The tab is greyed out when netFLEX is not installed on this
  host, and in preview mode. It opens but shows why the menu can't start while an
  install or upgrade is running, or while someone else (e.g. a console login) is
  running the boot menu, and refuses to start it while a check fix or report
  snapshot is running or just after an install/upgrade was launched.
  Install/upgrade launches and check fixes are refused while the boot menu is open.
- **Keys.** As on the console (cmm's profile ignores them), Ctrl-C, Ctrl-\ and
  Ctrl-Z go to the menu as ordinary keys; they never interrupt it or end the session.
- **One session, one browser.** The tool runs at most one boot menu session. Opening
  it in a second tab takes it over; the first tab says *Opened in another tab* and
  offers **Reconnect** (it never takes the session back by itself).
- **Reattach.** Closing the tab or the browser does not end the session. Showing the
  tab again reconnects and replays the recent screen output (256 KiB of
  scrollback) — of a running session, and also the output of an ended session,
  which stays viewable (with its exit code) until **Open boot menu** starts a new
  one.
- **End session** kills only the menu's own processes (the `su` the tool
  started, `new_boot_nms`, `boot_nms`). Anything the menu already started, such as boot or shutdown
  scripts, is not killed and may keep running. If a boot or shutdown appears to be
  in progress, the confirmation says so — ending it then can leave netFLEX half up
  or half down.
- **Tool stop.** Stopping or restarting the tool (Ctrl-C / SIGTERM) ends the boot
  menu session; a SIGHUP (e.g. the ssh session that started the tool dropping) does
  not. If the tool died without ending it, the next run finds the *leftover* menu
  and the tab offers **End it**, which likewise kills only the leftover
  `new_boot_nms` / `boot_nms` processes.
- **Browser.** The terminal needs **Firefox 115** or newer (or a current
  Chromium-based browser); in an older browser the tab says the terminal could not
  load. The rest of the UI is unaffected.
- **Audit.** Start, attach, detach, end (and refused starts) are recorded in
  `nfupgrader_audit.jsonl`; keystrokes are never recorded.

xterm.js (`xterm.js`, `xterm.css`, `addon-fit.js`) is vendored unmodified under
`nfupgrader/web/xterm/` (MIT, see `nfupgrader/web/xterm/LICENSE`); no external
resources are loaded.

## License tab

The **License** tab sets this host's netFLEX license (`/usr/cnc/data/lic.ucs`)
from the purchase email, typically right after an install. It drives netFLEX's
own tools (`lice`, `license -M`, `license-migrate`) and never edits the file
itself. The install form on the Config tab links here.

- **Fields.** The main section has the **Expiry date** (`MM/DD/YYYY`) and the
  **Trial** flag, **NE count** (`NumNE`, the NE limit netFLEX enforces), **Users**
  (`NumUsers`) and the **NE types** (a filterable checklist). Every other `lice`
  field (NEs available, weighted NEs, client/GUI users, other counts, feature
  flags, version, text) is under **Advanced**, with the values the tab does not
  edit (e.g. watch bits, NE groups) shown read-only.
- **States.** With no license the tab offers **Create from template** (copies
  `def.lic.ucs.linux64`, or `.linux`, owned by `cmm`). A license in an older
  format offers **Convert** (`license-migrate`). On a host without the netFLEX
  license tools it only says so.
- **Repair.** netFLEX 5.5.0.16's template ships an out-of-range expiry date, and
  `lice` crashes (SIGSEGV) reading any license with one — including a license
  just created from that template. The tab detects the crash and shows a
  **Repair license** box: enter the real expiry date and the license password,
  and it checks the password, backs up the license, sets the date with
  `license -n <file> -M`, and reads it back. Audited as `license_repaired` /
  `license_repair_failed`.
- **netFLEX must be down.** The license is shown read-only, with the reason, while
  netFLEX is running (`/usr/cnc/.CNC_UP`), while an install, upgrade, check fix,
  report snapshot or boot menu is running, and in preview mode. Stop netFLEX
  (e.g. from the Boot menu tab) to edit it.
- **Flow.** Before **Unlock**, the NE types list shows only the enabled types.
  **Unlock** checks the license password and lists every type (ticked =
  enabled); type changes are sent only after an Unlock. **Review changes** shows
  each field's before → after, plus any warnings (e.g. a type in an NE group);
  nothing is written. Only fields you changed are sent, and only NE types in
  the Unlock list (1-263) are ever switched on or off. Any edit after a review disables **Save** until you review
  again. **Save** asks for confirmation, then applies the changes and reads the
  license back: the result names what was applied, anything that did not take,
  the backup path, and any error (such as a wrong license password).
- **Password.** The license password is typed on the tab and sent only with
  Unlock, Review and Save. It is never stored (not in the browser, not in a URL,
  not on the server), never logged or audited, and reaches `lice`/`license` only
  on their standard input. It must be plain ASCII (as must Text and Version).
  The field is cleared after Save and whenever you leave
  the tab.
- **Backups.** Each Save first checks the password (a wrong one saves nothing
  and takes no backup), then copies the license to
  `/usr/cnc/data/lic.ucs.nfupgrader.<YYYYmmddHHMMSS>` (same owner and mode). The
  newest 10 are kept; older ones are removed. To roll back, copy a backup over
  `lic.ucs` with netFLEX down.
- **Audit.** `license_saved` (client, backup path, per-field before/after, NE type
  changes, expiry, what applied), `license_save_failed` (error, backup path if
  one was taken), `license_unlock_failed` (client; a failed Unlock or Review
  password), `license_created` and `license_migrated` are recorded in
  `nfupgrader_audit.jsonl`.

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
