# nfupgrader

Customer-facing web tool that wraps the netFLEX upgrade scripts so a customer can
upgrade the host they are logged into, with a consistent severity-prefixed log and
a read-only status view of every host in the site.

## Quick start

On a RHEL/Rocky 7, 8 or 9 host that can reach GitHub, **as root**:

1. **Install** (or update) the tool into `/opt/nfupgrader`:

   ```sh
   curl -fsSL https://github.com/dnuzzo-LR/nfupgrade/releases/latest/download/install.sh | bash
   ```

2. **Put the netFLEX CORE RPM** you want to install or upgrade to next to it:

   ```sh
   mv netFLEX-CORE-*.rpm /opt/nfupgrader/
   ```

3. **Start it:**

   ```sh
   cd /opt/nfupgrader && NFU_BIND=0.0.0.0 NFU_PORT=8099 ./run.sh
   ```

   It prints a single-use link such as
   `http://<host>:8099/auth?token=…`. Open it in a browser on your desktop.
   It also prints the `ssh -L` command for reaching it through an SSH tunnel
   instead, with the matching `http://127.0.0.1:<port>/…` link. (Without
   `NFU_BIND`/`NFU_PORT` it listens on loopback only and the tunnel is the only
   way in. With `NFU_BIND=0.0.0.0` the direct link and the pages travel as plain
   HTTP; the tunnel route is encrypted.)

4. **Use the tabs:** on a host without netFLEX the tool offers a new
   **Install**; on an installed host, **Stage** then **Upgrade**. The
   **License** tab sets the license from the purchase email, and the **Boot
   menu** tab runs netFLEX's boot menu in the page. Details are below.

Re-running the `curl` command updates the tool in place; `logs/` (audit log,
saved configs, report snapshots) and any RPMs in `/opt/nfupgrader` are kept.
`/opt/nfupgrader/VERSION` shows the installed version.

## Layout

| Path | What |
|------|------|
| `nf_upgrade`, `nf_upgrader` | Forks of netFLEX `inc_upgrade` / `inc_upgrader`, behavior-identical to the originals but with a cleaned-up, severity-prefixed log. Intended to eventually replace the originals in the product. |
| `nf_install` | New (not a fork): fresh netFLEX CORE install of the local host, with resume and reset. See *Installing on a new host*. |
| `nf_patch` | New (not a fork): checks and installs one `netFLEX-CORE-PATCH` RPM on the local host (`nf_patch -f <rpm>`). See *Patches tab*. |
| `nf_log.ksh` | Sourced logging helpers: `loginfo` / `logwarn` / `logerror` / `logfatal` and the status-preserving `run_logged` sub-command wrapper. |
| `ref/inc_upgrade`, `ref/inc_upgrader` | Pinned snapshots of the originals (netFLEX commit `776f42242`), used only as the baseline for the equivalence test. Never run. |
| `test/` | ksh unit tests and the fork verifier. |

## Constraints

- Shell is ksh, written **ksh88-safe** (`/usr/bin/ksh` on customer boxes). No `PIPESTATUS`, no `set -o pipefail`, no arrays in the shipped scripts.
- The Python service (later phases) targets **Python 3.6.8**, stdlib only — no third-party packages, no build step.
- No nmake here — this repo is standalone; ignore any netFLEX `BASE`/`VPATH` build environment.

## Installing nfupgrader on a host

On a RHEL/Rocky 7, 8 or 9 host that can reach GitHub, as root:

```sh
curl -fsSL https://github.com/dnuzzo-LR/nfupgrade/releases/latest/download/install.sh | bash
```

Pin a version with `…/releases/download/v<version>/install.sh` instead of
`latest`. The installer checks it is root on a supported OS with `python3`,
`tar` and `sha256sum`; downloads `nfupgrader-<version>.tar.gz` from the same
release and refuses (installing nothing) if its SHA-256 doesn't match the one
built into the script; and unpacks it into `/opt/nfupgrader` (override with
`NFU_INSTALL_DIR`). Re-running it updates in place: release files are
overwritten, while `logs/`, any `netFLEX-CORE*.rpm` and anything else you put
there are left alone. It writes `/opt/nfupgrader/VERSION` and does not start the
tool. Release tarballs contain the tool only, not the tests.

### Installing from a downloaded zip (or without GitHub access)

GitHub offers two different zips; which one you get matters:

| Download | What it contains | Needs a release? |
|----------|------------------|------------------|
| Repo page → **Code → Download ZIP** | `main` as it is *right now*, unpacks to `nfupgrade-main/`, no version number, includes `test/`, `tests/` and `ref/` | No — anything pushed to `main` is in it |
| Release page → **Source code (zip)** | the tagged release, unpacks to `nfupgrade-<version>/`, also includes the tests | Yes |
| Release page → `nfupgrader-<version>.tar.gz` + `install.sh` | the tool only, checksum-verified by `install.sh` | Yes |

Use **Download ZIP** for a quick lab test of an unreleased fix; give customers a
**release**, so there is a version to quote and the checksum is checked.

To install from a zip, copy it to the host and, as root:

```sh
cd /tmp && unzip -q nfupgrade-main.zip             # `dnf install unzip` if missing
mkdir -p /opt/nfupgrader
cp -a nfupgrade-main/. /opt/nfupgrader/            # logs/ and RPMs already there are kept
cd /opt/nfupgrader && ./run.sh
```

Execute bits survive the zip. Nothing writes `/opt/nfupgrader/VERSION` this way,
so it keeps showing the last release installed with `install.sh` (or is absent);
note which commit you copied.

For a host that cannot reach GitHub, prefer the release files: download
`nfupgrader-<version>.tar.gz` and `install.sh` from the release page, copy both
to the host, and run (as root):

```sh
NFU_TARBALL=./nfupgrader-<version>.tar.gz bash install.sh
```

`install.sh` then uses the local tarball instead of downloading, still checks
its SHA-256, and writes `VERSION`.

### Making a release

```sh
git tag -a v1.0.0 -m "nfupgrader 1.0.0" && git push origin v1.0.0
sh tools/make_release.sh 1.0.0     # builds dist/nfupgrader-1.0.0.tar.gz + dist/install.sh
gh release create v1.0.0 --repo dnuzzo-LR/nfupgrade --title "nfupgrader 1.0.0" \
    --latest dist/nfupgrader-1.0.0.tar.gz dist/install.sh
```

The release must not be a pre-release: the one-liner uses
`releases/latest`, which skips pre-releases (fix one with
`gh release edit v<ver> --prerelease=false --latest`).
`make_release.sh` requires the tag to exist and the working tree to be clean;
it fills the version, the tarball's checksum and the repo (from `origin`) into
`tools/install.sh` and prints the `gh release create` command to run.

## Running the service on a host

Install it as above, or copy the whole repo to the host (keep the layout — the
`nfupgrader/` package and the `nf_upgrade` / `nf_upgrader` / `nf_log.ksh`
scripts must stay siblings), then:

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
| `NFU_DEPOT_DIRS` | directory `run.sh` was started from, then `/opt/apt/upgrade` | Colon-separated directories scanned (top level only) for `netFLEX-CORE*.rpm` (the CORE list excludes any file with `PATCH` in its name; the Patches tab scans the same directories for `*PATCH*.rpm`). The Config tab lists them automatically on page load; **Refresh depots** re-scans. Picking one fills `DEPOTFILE`, `NEW_GENERIC` and `NEW_LOAD` from the filename. Missing directories are skipped. |
| `NFU_PREVIEW_INSTALL` | (unset: off) | Preview the install screens on any box, even one already running netFLEX. Set to `1` (or `before`, `running`, `failed`, `done`). The UI shows install mode with sample data and a yellow PREVIEW banner whose picker switches between those scenarios. Pre-flight checks run for real (read-only). **Launch, Resume, Reset and Fix are refused**; Save writes `nfupgrader_preview.cfg` / `nfupgrader_preview_cnc.cnfg` beside — never over — the real saved config. |
| `NFU_REPORTS` | `nfupgrader/reports.default.json` | Report list for the Reports tab. Snapshots go to `${INCLOGDIR}/nfupgrader_reports/` (newest 20 kept); an Upgrade launch takes a *before* snapshot first and an *after* snapshot when the upgrade ends. |

**Loopback (default):** the startup output gives the `ssh -L` tunnel command and the
`http://127.0.0.1:<port>/auth?token=…` link to open once it is up. **Non-loopback:**
the URL uses the hostname and a plaintext-HTTP warning is printed (token/URL cross
the network unencrypted); the tunnel command and its loopback link are printed too,
as the encrypted alternative. Either way the token is single-use, so open only one
of the two links. The tunnel command names `NFU_URL_HOST` if set, else this host's
name, prefixed with `$SUDO_USER@` when started through `sudo` by a non-root user
(otherwise ssh uses your workstation login name); it tunnels to `127.0.0.1`, or to
the bind address when `NFU_BIND` is a specific interface. The session cookie is
bound to the client IP.

### Is netFLEX up?

The tool uses netFLEX's own rule (the boot menu, `boot_nms`, rewrites
`.CNC_UP` from the same check): netFLEX is **up** only when `/usr/cnc/.CNC_UP`
exists **and** a process named `AT`, `sdi`, `dacs`, `screen`, `arm`, `bill` or
`dump` is running **as `cmm`** (so a support user's own `screen` session does not
count). `.CNC_UP` with none of those running is **stale** — e.g. left behind by a
forced stop or an interrupted boot — and is treated as down: the header shows
**APP DOWN (stale .CNC_UP)**, the License tab is editable, and reboot-load patches
are not blocked (`nf_patch` logs a warning). The tool never deletes the file;
opening netFLEX's boot menu clears it. This rule drives the header badge,
Overview and Site view, the License tab, the Patches tab, `nf_patch`, and the
`post-app-up` post-flight check. If `ps` itself fails, the file is trusted.

**Launch is guarded** by a browser confirm dialog, but there is no other gate — a
confirmed click runs the real upgrade immediately. Only click on a host prepared
for it. Just running the service launches nothing; it only observes.

### Example — reach it by hostname on a lab box

```sh
export NFU_BIND=0.0.0.0 NFU_PORT=8099
export NFU_USER_CONFIG=/opt/apt/upgrade/my.cfg   # only needed to Launch
./run.sh    # browse the printed http://<host>:8099/auth?token=… URL
```

### Example — through a bastion on a STIG host (no SSH forwarding)

STIG-hardened hosts set `AllowTcpForwarding no` in sshd (check with
`sshd -T | grep -i allowtcpforwarding`), so the printed
`ssh -L <port>:127.0.0.1:<port> <host>` fails with
`channel 3: open failed: administratively prohibited`. Forward through the
bastion instead; the bastion then connects to the tool over the network, so the
tool must listen on the host's own IP and the firewall must let the bastion in.
Below, `<netflex-ip>` is the netFLEX host's IP as the bastion sees it,
`<bastion-private-ip>` the bastion's address as the netFLEX host sees it, and
`<bastion>` what you ssh to from your PC (e.g. `ubuntu@<bastion-public-ip>`).

1. On the netFLEX host, start the tool on its own IP:

   ```sh
   cd /opt/nfupgrader && NFU_BIND=<netflex-ip> NFU_PORT=8099 ./run.sh
   ```

2. On the netFLEX host, let only the bastion reach the port. This is a
   runtime-only rule (gone on reboot or `firewall-cmd --reload`); without it a
   `drop` zone silently drops the connection:

   ```sh
   firewall-cmd --add-rich-rule='rule family="ipv4" source address="<bastion-private-ip>/32" port port="8099" protocol="tcp" accept'
   ```

3. From the bastion, confirm it is reachable (times out before step 2,
   succeeds after):

   ```sh
   nc -zv -w 5 <netflex-ip> 8099
   ```

4. On your PC, tunnel through the bastion only:

   ```sh
   ssh -L 8099:<netflex-ip>:8099 <bastion>
   ```

5. In your browser, open the printed link with the host changed to `127.0.0.1`:
   `http://127.0.0.1:8099/auth?token=<token>` (the printed tunnel section
   already shows this form). PC to bastion is encrypted by ssh; **bastion to
   netFLEX host is plain HTTP**, token included.

6. When finished, stop the tool (Ctrl-C) and remove the rule:

   ```sh
   firewall-cmd --remove-rich-rule='rule family="ipv4" source address="<bastion-private-ip>/32" port port="8099" protocol="tcp" accept'
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

With the web GUI selected, the web GUI step also does the netFLEX install guide's
manual steps 11, 16 and 17 (a failure in any of them only warns, naming the step to
do by hand):

- **11, console ssh**: adds this host's ssh key (short name and FQDN, via
  `ssh-keyscan`) to cmm's `known_hosts`, then copies `/usr/cnc/cmm/.ssh` and
  `/usr/cnc/login/.profile` into `/usr/cnc/login/console/`. The GUI logs in as
  `console` over ssh to its own host.
- **16, `httpd.conf`**: `User cmm` / `Group cmm` and `Require all granted` in
  `<Directory />`. netFLEX's `webgui.conf` grants no access itself (stock Apache
  answers 403), and the setuid-`cmm` `web_intrfc` cannot open its ssh pseudo-terminal
  (`grantpt()` EPERM) unless Apache's real user is `cmm`.
- **17, `00-mpm.conf`**: prefork instead of event; with event, CGIs go through
  `mod_cgid`'s socket in `/run/httpd`, which a `cmm` worker cannot reach.

Each file it changes is backed up as `<file>.pre-nfinstall.<timestamp>`; already-set
files are left alone. If the result does not check out or `apachectl configtest`
fails, both files are put back. httpd is then restarted. Reset does not undo these.

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
  netFLEX is running (see *Is netFLEX up?*), while an install, upgrade, check fix,
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

## Patches tab

The **Patches** tab (after Upgrade) installs a netFLEX patch RPM from the depot
directories (`NFU_DEPOT_DIRS`) on **this host**. It lists every `*PATCH*.rpm` found
(top level only) with its version and a status; only the one eligible patch has an
**Install** button. Install asks for confirmation, then launches `nf_patch`
detached; the Log tab follows its trace. **netFLEX stays up** during the install, but
the patch restarts its processes. The tab is greyed in install mode (netFLEX not
installed) and Install is refused in preview mode.

- **Eligibility rules.** Identity comes from `rpm -qp` on the file, not the file name.
  Only `netFLEX-CORE-PATCH` is supported. A patch must match the installed CORE's
  release (generic, e.g. `5.4.1`) and architecture (the installed `netFLEX-CORE`
  arch, e.g. `i686` or `x86_64`). Order is strict: with a PATCH installed only the
  next load (current + 1) is offered; with none installed, only the lowest patch
  above the CORE load (a missing first patch cannot be detected). Others show as
  installed, out of sequence ("install X first"), wrong release or wrong arch.
- **Host blocks.** A banner replaces the Install buttons when netFLEX-CORE is not
  installed, a `netFLEX-CORE-IPATCH` is installed (contact support), an upgrade lock
  (`/usr/cnc/.UPGRADE_LOCK`) exists, or an upgrade is staged (`/usr/cnc_stage`).
  Install is also refused while an install, upgrade, patch, check fix, report
  snapshot or boot menu session is running, and for a short grace period just
  after an upgrade, stage or install was launched.
- **Needs stop.** If the patch's `REBOOT_LOADS` applies and netFLEX is up (see
  *Is netFLEX up?*), Install
  is disabled with "This patch requires netFLEX to be stopped — use the Boot
  menu tab". (Best-effort; the RPM's own refusal is the backstop.)
- **Not supported.** `IPATCH`, `INC` and `ATTEXT` patches are listed, marked not
  supported, and never installed.
- **`nf_patch`.** `nf_patch -f <rpm> [-l <logdir>] [-n]`; `-n` checks only. The log
  directory is `-l`, else `$INCLOGDIR`, else `/usr/cnc/logs`. It re-runs the rules
  above, then `rpm -Uvh --replacepkgs --replacefiles` (the options support uses
  for a manual install; a patch overwrites files owned by
  `netFLEX-CORE`; without the flag rpm refuses with "conflicts with file from
  package" and exits 4), then verifies the new version is reported. Exit codes:
  `0` installed (or eligible with `-n`), `1` refused before rpm was touched, `2`
  rpm -Uvh failed or the post-install check did not show the new version. The trace is `<logdir>/patch_inc_<host>.<pid>`; its last line is
  `RESULT: installed <version>`, `RESULT: refused: <reason>` or
  `RESULT: failed: <reason>`. On refused/failed the tab shows the trace path to send
  to support.
- **Audit.** `patch_install_started` (client, file, version), `patch_install_refused`
  (reason), `patch_install_done` and `patch_install_failed` (exit code, trace path;
  or the error when `nf_patch` could not be started)
  in `nfupgrader_audit.jsonl`. The service waits on `nf_patch` in a background
  thread, so the done/failed record is written as soon as it exits (and no
  `<defunct>` process is left behind).

## Testing

```sh
ksh test/nf_log_test.ksh                          # unit tests for nf_log.ksh
ksh test/nf_install_test.ksh                      # nf_install in a sandbox
ksh test/nf_patch_test.ksh                        # nf_patch in a sandbox (fake rpm)
ksh test/install_test.ksh                         # make_release.sh + install.sh in a sandbox
sh  test/nf_fork_verify.sh                         # fork syntax/equivalence/coverage/grammar
python3 -m unittest discover -s tests -p 'test_*.py'   # Phase 2 python tests
```

`nf_fork_verify.sh` proves the forks are behaviorally identical to the pinned
originals in `ref/` (skeleton diff), that every script emit was converted
(coverage), and that converted/emitted lines match the log grammar.
