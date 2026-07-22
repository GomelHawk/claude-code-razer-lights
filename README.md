# Claude Code Razer Lights

![CI](https://github.com/GomelHawk/claude-code-razer-lights/actions/workflows/ci.yml/badge.svg)

Two **independent** [Claude Code](https://www.claude.com/product/claude-code)
companions in one repo — use either on its own:

1. **Razer lighting** — drives your Razer device LEDs from Claude Code's status via
   the Chroma REST SDK. *Windows/WSL; needs Razer hardware + Synapse.*
2. **Tray app + usage stats** — a system-tray / menu-bar icon that mirrors the same
   status, plus a click-to-open panel with your Claude usage (5-hour, weekly, and
   credit limits — the same data as `/usage`). *Windows, Linux, and macOS; needs no
   Razer hardware.*

They share only a tiny local status server, so **the lights work without the tray,
and the tray + usage work without any Razer device.**

**Set up for your platform:** [Windows / WSL](#setup) · [Ubuntu / GNOME](#native-linux-ubuntu--gnome) · [macOS](#macos) · [prebuilt Windows exe](#prebuilt-executables-no-python-needed) · [tray + usage](#tray-app-claude-spark-icon--usage)

Both the lights and the tray icon show the same Claude status:

- 🟢 **Green** — idle (Claude finished, waiting for you)
- 🟡 **Yellow** — working
- 🔴 **Red, blinking** — Claude is waiting for confirmation (a permission prompt)
- ⚫ **No session** — lights release to your normal Synapse lighting; the tray icon shows the neutral Claude terracotta

When no Claude Code session is running, the script holds no Chroma session, so
Synapse drives your lighting exactly as it normally would. The lights are only
taken over while at least one Claude Code session is active.

Tested with a **Razer Viper Mini** mouse and a **Razer Kraken V3** headset on
**Windows 11**, but it works with any Chroma-capable device — just edit the
`DEVICES` list.

## How it works

```
Claude Code hooks ──> hook.sh (extracts session_id) ──> light server ──> Razer Chroma SDK ──> devices
```

A small local HTTP server holds a single Chroma SDK session and keeps it alive
with heartbeats. Claude Code [hooks](https://code.claude.com/docs/en/hooks) fire
on lifecycle events (session start/end, prompt submit, tool use, stop, permission
prompt). Each hook runs `hook.sh`, which reads the `session_id` from the JSON
payload Claude Code delivers via stdin, then calls the server with `?sid=<id>`.

The server tracks state per session (`idle`, `working`, or `confirm`) and applies
a priority rule to pick the color:

- If **any** session is `confirm` → red blink (regardless of other sessions)
- Else if **any** session is `working` → yellow
- Else → green

This means if one window is waiting for your confirmation while another is
actively working, the lights keep blinking red until you resolve the confirmation.
Control is only released when the **last** Claude Code window closes, and a
watchdog force-releases the lights if a session dies without firing its end hook.

Claude Code has no hook that fires at the exact moment you answer a permission
prompt — the next event after your answer is the tool actually running, i.e.
`PostToolUse` (on approval) or `PostToolUseFailure` (on denial/error). Both are
wired to `working` so the red blink clears as soon as possible after you answer,
instead of waiting for the next unrelated `PreToolUse`/`Stop` hook.

## Tray app (Claude spark icon + usage)

A separate Windows tray companion, `tray_app.py`, shows the **Claude spark icon**
tinted to mirror the Razer lighting, plus a click-to-open **usage flyout**:

- 🟢 green — idle · 🟡 yellow — working · 🔴 red, blinking — waiting for
  confirmation · 🟠 Claude terracotta — no active session
- **Click the icon** to open a card with your Claude usage — the same 5-hour,
  weekly, and usage-credit figures the `/usage` command shows, with reset timers.
- Optional soft **chime** when a session needs confirmation (right-click menu).

The icon's state comes from a read-only `GET /state` endpoint on the light server
(added for this; it never affects the lighting). Usage comes from `usage.py`,
which calls the same endpoint Claude Code's `/usage` uses and falls back to
reconstructing totals from the local transcripts if that is unavailable.

The flyout mirrors Claude Code's `/usage` card (example values):

```text
┌────────────────────────────────────────────────┐
│ Your usage limits · Team                       │
│                                                │
│ 5-hour limit        Resets in 3 hr 12 min  35% │
│ ████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ │
│                                                │
│ Weekly · all models    Resets Fri 4:00 PM  42% │
│ ███████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░ │
│                                                │
│ Usage credits                   $1.20 of $2.00 │
│ ████████████████████████████░░░░░░░░░░░░░░░░░░ │
└────────────────────────────────────────────────┘
```

Bars are blue, turning amber near a limit and red when critical. "Usage credits"
is the monthly overage pool (shown only if your plan has one). If the official
numbers can't be fetched, the card falls back to token/cost totals labelled
"estimate".

```powershell
pip install PySide6
python C:\razer-lights\tray_app.py
```

`usage.py` also runs standalone for a terminal readout:

```powershell
python C:\razer-lights\usage.py          # live 5-hour / weekly / credits
python C:\razer-lights\usage.py --json   # raw endpoint response
python C:\razer-lights\usage.py --fallback   # force the local-transcript estimate
```

**WSL note:** the server and tray run on Windows, but Claude Code's logs and
OAuth token live in WSL. Rather than set `CLAUDE_HOME` every launch, drop a
`tray_config.json` next to `tray_app.py` (see
[`tray_config.example.json`](tray_config.example.json)):

```json
{
  "claude_home": "\\\\wsl.localhost\\Ubuntu\\home\\<user>\\.claude",
  "state_url": "http://127.0.0.1:8777/state"
}
```

Resolution order is env var → `tray_config.json` → default. The tray reads the
OAuth token fresh from `.credentials.json` on each poll; Claude Code refreshes
that file while it runs, so no separate login is needed.

**Notes:**

- The **icon follows the light server** — if `razer_light_server.py` isn't
  running (or no Claude session is active), the icon shows the neutral Claude
  terracotta; the tooltip says which.
- Usage is polled infrequently (every ~5 min, plus on open when stale) because
  the usage endpoint rate-limits (`429`); the last official figures are cached to
  `usage_cache.json`, so the card is never blank and a `429` never replaces good
  data with the estimate.
- Diagnostics are written to `tray_app.log` next to the script.

### No Razer devices? (status + usage only)

You don't need any Razer hardware or Synapse to use the tray icon and usage
stats — the light server doubles as a status broker. Run the server and the tray
as usual: the icon still turns green/yellow/red/terracotta with Claude's state and
the flyout still shows your usage; only the physical lights are skipped.

If Chroma isn't reachable the server detects it and backs off (it won't retry on
every hook), so status stays responsive. To skip the Chroma SDK entirely and keep
the log clean, start the server with `RAZER_LIGHTS=0`:

```powershell
$env:RAZER_LIGHTS = "0"; python C:\razer-lights\razer_light_server.py
```

(For the scheduled task, add `RAZER_LIGHTS=0` to the task's environment, or leave
it out — the automatic back-off makes device-less operation fine either way.)

### Prebuilt executables (no Python needed)

Tagged releases ship Windows executables built by CI
([`.github/workflows/build.yml`](.github/workflows/build.yml)) — end users need
no Python or `pip install`:

1. Download `ClaudeRazerTray.exe`, `RazerLightServer.exe`, and
   `tray_config.example.json` from the latest Release.
2. Put them in a folder (e.g. `C:\razer-lights`), rename
   `tray_config.example.json` → `tray_config.json`, and set `claude_home` to your
   WSL `.claude` path.
3. Run `RazerLightServer.exe` (or register it as the `RazerLights` scheduled task,
   substituting the exe for `pythonw.exe … razer_light_server.py`), then run
   `ClaudeRazerTray.exe`.

Config, logs, and the usage cache are written next to the executables.

Build them yourself on a Windows machine:

```powershell
pip install pyinstaller pyside6 requests
pyinstaller --onefile --windowed --name ClaudeRazerTray --add-data "assets/claude_spark.png;assets" tray_app.py
pyinstaller --onefile --windowed --name RazerLightServer razer_light_server.py
```

Or run the **Build Windows exe** workflow (Actions → Run workflow) for downloadable
artifacts; pushing a `vX.Y.Z` tag attaches the exes to a GitHub Release.

## Requirements

- Windows 10/11 + WSL
- **Razer Synapse** (3 or 4) running, with **Chroma Connect** installed and
  app/SDK control enabled for your devices. *Synapse must be running for the
  lights to change* — if it is closed, SDK calls silently succeed but nothing
  lights up.
- Python 3.9+ and `pip install requests` (plus `pip install PySide6` for the
  optional tray app)
- Claude Code

## Setup

You can run this **two ways** — pick one:

- **From source (Python):** copy the `.py` files and run them with Python. The
  steps below use this path.
- **Prebuilt executables (no Python):** download `RazerLightServer.exe` and
  `ClaudeRazerTray.exe` — see
  [Prebuilt executables](#prebuilt-executables-no-python-needed). The same steps
  apply; just run the `.exe` instead of `python …` (each step notes the exe
  equivalent).

Either way, the **light server** runs on **Windows** (it needs the Chroma SDK) and
the **hook script** runs in **WSL** (where Claude Code executes hooks). The WSL
hook step is identical for both paths.

### 1. Install

**Windows (from source)** — in PowerShell:

```powershell
mkdir C:\razer-lights
copy razer_light_server.py C:\razer-lights\
copy usage.py C:\razer-lights\
copy tray_app.py C:\razer-lights\
xcopy /I assets C:\razer-lights\assets    # the tray icon asset
pip install requests PySide6
```

**Windows (prebuilt exe)** — instead of the above, put `RazerLightServer.exe` and
`ClaudeRazerTray.exe` in `C:\razer-lights\` (nothing to `pip install`; the icon
asset is bundled inside the exe). See
[Prebuilt executables](#prebuilt-executables-no-python-needed).

**WSL (both paths)** — in bash; the hook just talks to the server over HTTP:

```bash
mkdir -p /home/user/razer-lights
cp hook.sh /home/user/razer-lights/
chmod +x /home/user/razer-lights/hook.sh
```

Then update the paths in `claude-settings.example.json` to match your WSL location
before merging it into your Claude Code settings.

### 2. Verify the Chroma SDK responds

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:54235/razer/chromasdk" `
  -ContentType "application/json" `
  -Body '{"title":"test","description":"t","author":{"name":"x","contact":"x"},"device_supported":["mouse","headset"],"category":"application"}'
```

This should return a `sessionid` and a `uri`. If it errors or hangs, Synapse /
Chroma Connect isn't running.

### 3. Smoke-test the server

Run it in a visible window so you can see its diagnostics:

```powershell
python C:\razer-lights\razer_light_server.py
```

(Prebuilt exe: run `C:\razer-lights\RazerLightServer.exe` — it's windowless, so
watch `razer_light_server.log` next to it instead of console output.)

In a second PowerShell window, walk through the states (use `curl.exe`, not the
PowerShell `curl` alias). Each request passes a `?sid=` to identify the session:

```powershell
curl.exe "http://127.0.0.1:8777/session-start?sid=A"  # devices go green
curl.exe "http://127.0.0.1:8777/working?sid=A"        # yellow
curl.exe "http://127.0.0.1:8777/confirm?sid=A"        # red blinking
curl.exe "http://127.0.0.1:8777/idle?sid=A"           # green
curl.exe "http://127.0.0.1:8777/session-end?sid=A"    # release to Synapse default
```

To verify multi-session priority, simulate two sessions:

```powershell
curl.exe "http://127.0.0.1:8777/session-start?sid=A"
curl.exe "http://127.0.0.1:8777/session-start?sid=B"
curl.exe "http://127.0.0.1:8777/confirm?sid=A"        # red blinking
curl.exe "http://127.0.0.1:8777/working?sid=B"        # still red (A needs attention)
curl.exe "http://127.0.0.1:8777/idle?sid=A"           # now yellow (only B working)
curl.exe "http://127.0.0.1:8777/idle?sid=B"           # green
```

### 4. Add the Claude Code hooks

Merge the contents of [`claude-settings.example.json`](claude-settings.example.json)
into your `%USERPROFILE%\.claude\settings.json` (create the file if it doesn't
exist). For project-only use, put it in `.claude/settings.json` inside the
project instead.

### 5. Run at login (windowless)

Two things can auto-start: the **light server** (background service) and the
**tray app** (system-tray icon). The commands below use the prebuilt exes; if you
run from source, swap in `pythonw.exe` as noted in the comments.

**Light server — Scheduled Task.** In an **Administrator** PowerShell:

```powershell
$action = New-ScheduledTaskAction `
    -Execute "C:\razer-lights\RazerLightServer.exe" `
    -WorkingDirectory "C:\razer-lights"
# From source instead of the exe:
#   -Execute "C:\Python314\pythonw.exe" -Argument "C:\razer-lights\razer_light_server.py"

$trigger = New-ScheduledTaskTrigger -AtLogOn
$trigger.Delay = "PT10S"   # wait 10 s for Synapse to start

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "RazerLights" -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited -Force
Start-ScheduledTask -TaskName "RazerLights"
```

Use the **full path** to the executable so the task doesn't depend on `PATH` in the
scheduler's environment. The 10-second delay gives Razer Synapse time to start
before the server opens a Chroma session. The server exe is built `--windowed`, so
it has no console (from source, use `pythonw.exe`, not `python.exe`, for the same
effect). Diagnostics go to `razer_light_server.log` next to the executable.

**Tray app — Startup shortcut.** The tray needs to run in your interactive desktop
session so its icon appears, so a Startup-folder shortcut is simpler than a task
(no admin required):

```powershell
$startup = [Environment]::GetFolderPath('Startup')
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut("$startup\ClaudeRazerTray.lnk")
$sc.TargetPath = "C:\razer-lights\ClaudeRazerTray.exe"
$sc.WorkingDirectory = "C:\razer-lights"
# From source instead: TargetPath = pythonw.exe, and
#   $sc.Arguments = "C:\razer-lights\tray_app.py"
$sc.Save()
```

The `-Force` flag overwrites any existing `RazerLights` task, so the server command
is safe to re-run whenever you change the action/trigger/settings. Without it,
re-registering fails with a "file already exists" error (`HRESULT 0x800700b7`).

If the server is already running when the trigger fires (e.g. after an unlock
rather than a full logout), the new instance detects the occupied port, logs a
message, and exits cleanly so the task doesn't show as failed.

To update the exes later: `Stop-ScheduledTask -TaskName RazerLights`, replace the
files, then `Start-ScheduledTask -TaskName RazerLights` (and relaunch the tray).

### 6. Test through Claude Code

Open Claude Code in a project. You should see green on open, yellow while it
works, red blink on a permission prompt, green when done, and a return to your
normal Synapse profile when the last session closes.

## Configuration

Edit the constants at the top of `razer_light_server.py`:

| Constant | Default | Meaning |
| --- | --- | --- |
| `DEVICES` | `("mouse", "headset")` | Chroma device endpoints to drive. Options include `mouse`, `keyboard`, `headset`, `mousepad`, `keypad`, `chromalink`. |
| `WATCHDOG_TIMEOUT` | `600` | Seconds of hook silence before the lights are force-released (crash safety). |
| `LISTEN` | `127.0.0.1:8777` | Where the local server listens. |
| `RAZER_LIGHTS` (env) | `1` | Set to `0` to skip the Chroma SDK entirely (status + usage still work; no devices needed). |

Colors are set in `set_color` / the handler (note the Chroma SDK uses **BGR**,
not RGB).

## Troubleshooting

- **Scheduled task shows exit code 1 / server not running** — check
  `razer_light_server.log` in the script directory for the error. Common causes:
  Python not found (use the full path to `pythonw.exe`), or the port was already
  in use (the log will say so and the exit code will be 0 in that case — a
  previous instance is still running).
- **SDK returns `result: 0` but nothing lights up** — Synapse isn't running, or
  the device isn't on app/Chroma-controlled lighting (onboard/fixed effects
  override the SDK). Open Chroma Studio and make sure the device is
  app-controlled.
- **Only some devices respond** — confirm each one is enabled in Chroma Connect
  and listed in `DEVICES`.
- **Lights stay stuck on after closing Claude Code** — a session probably
  crashed without firing `SessionEnd`; the watchdog clears it after
  `WATCHDOG_TIMEOUT`. Lower the value if you want a faster release.
- **Manual testing leaves things confused** — each manual SDK `POST` opens a
  session that you must `DELETE`; orphaned sessions interfere with lighting.
  Restart Synapse to clear them, and prefer testing through the running server.

## Native Linux (Ubuntu / GNOME)

The **tray icon + usage stats** work on native Ubuntu, and it's simpler than the
Windows/WSL setup because everything runs on one machine — no WSL boundary, no
UNC path, `CLAUDE_HOME` defaults to `~/.claude`.

> **Physical device lighting is not available on Linux yet.** The Chroma REST SDK
> is Windows-only, so run the server in status-only mode (`RAZER_LIGHTS=0`). A
> cross-platform lighting backend (OpenRGB — also covers Logitech) is planned;
> until then the sphere/tray status and usage work, but no LEDs are driven.

### 1. Install

```bash
sudo apt install python3 python3-pip
pip install --user requests PySide6
mkdir -p ~/razer-lights
cp razer_light_server.py usage.py tray_app.py hook.sh ~/razer-lights/
cp -r assets ~/razer-lights/
chmod +x ~/razer-lights/hook.sh
```

Edit `~/razer-lights/hook.sh` and change `curl.exe` → `curl` (native Linux has
plain `curl`, not the Windows one).

### 2. Hooks

Same as the Windows steps: merge `claude-settings.example.json` into
`~/.claude/settings.json`, pointing each command at
`/home/<user>/razer-lights/hook.sh`.

### 3. GNOME tray support

GNOME dropped legacy tray icons, so Qt's tray needs an AppIndicator extension.
Ubuntu usually ships it — just make sure it's enabled:

```bash
# Ubuntu (often preinstalled):
gnome-extensions enable ubuntu-appindicators@ubuntu.com
# Upstream GNOME:
sudo apt install gnome-shell-extension-appindicator && \
  gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com
```

(KDE, XFCE, Cinnamon, etc. show tray icons natively — no extension needed.)

### 4. Autostart at login (systemd user services)

Create `~/.config/systemd/user/razer-lights.service`:

```ini
[Unit]
Description=Claude Code status server
[Service]
Environment=RAZER_LIGHTS=0
ExecStart=%h/.local/bin/python3 %h/razer-lights/razer_light_server.py
Restart=on-failure
[Install]
WantedBy=default.target
```

And `~/.config/systemd/user/razer-lights-tray.service`:

```ini
[Unit]
Description=Claude usage tray
After=razer-lights.service graphical-session.target
PartOf=graphical-session.target
[Service]
ExecStart=/usr/bin/python3 %h/razer-lights/tray_app.py
Restart=on-failure
[Install]
WantedBy=graphical-session.target
```

Enable both (use the correct `python3` path — `which python3`):

```bash
systemctl --user daemon-reload
systemctl --user enable --now razer-lights.service razer-lights-tray.service
journalctl --user -u razer-lights-tray.service -f   # logs
```

The tray icon turns green/yellow/red/terracotta with Claude's state and the usage
flyout works exactly as on Windows — the server just runs as a status broker
without touching any devices.

## macOS

The **tray icon + usage stats** work on macOS too — and the tray is nicer here: Qt
renders it as a native **menu-bar item**, so no GNOME-style extension is needed.

> **No physical lighting on macOS.** Razer discontinued Synapse for macOS (no
> Chroma SDK), so run status-only with `RAZER_LIGHTS=0`. The menu-bar status and
> usage flyout work exactly as elsewhere.

Install and hooks are the same as the [Linux steps](#native-linux-ubuntu--gnome)
(`pip install requests PySide6`, copy the files, use plain `curl` in `hook.sh`,
merge `claude-settings.example.json`). Use a real Python (e.g. Homebrew
`/opt/homebrew/bin/python3`), not the Xcode stub.

**Autostart at login (launchd).** Create
`~/Library/LaunchAgents/com.claude.razer-server.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.claude.razer-server</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/bin/python3</string>
    <string>/Users/<user>/razer-lights/razer_light_server.py</string>
  </array>
  <key>EnvironmentVariables</key><dict><key>RAZER_LIGHTS</key><string>0</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
</dict></plist>
```

And `~/Library/LaunchAgents/com.claude.razer-tray.plist` (same shape, `Label`
`com.claude.razer-tray`, `ProgramArguments` pointing at `tray_app.py`, no
`EnvironmentVariables`). Load both:

```bash
launchctl load ~/Library/LaunchAgents/com.claude.razer-server.plist
launchctl load ~/Library/LaunchAgents/com.claude.razer-tray.plist
```

## Notes

This project is not affiliated with or endorsed by Razer or Anthropic. "Razer",
"Chroma", "Synapse", "Viper", and "Kraken" are trademarks of Razer Inc.

## License

MIT — see [LICENSE](LICENSE).
