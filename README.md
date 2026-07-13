# Claude Code Razer Lights

![CI](https://github.com/GomelHawk/claude-code-razer-lights/actions/workflows/ci.yml/badge.svg)

Drive your Razer device lighting from [Claude Code](https://www.claude.com/product/claude-code)
status, using the Razer Chroma REST SDK on Windows/WSL.

- 🟢 **Green** — idle (Claude finished, waiting for you)
- 🟡 **Yellow** — working
- 🔴 **Red, blinking** — Claude is waiting for confirmation (a permission prompt)
- ⚫ **No session** — control is released back to your normal Synapse lighting

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

## Requirements

- Windows 10/11 + WSL
- **Razer Synapse** (3 or 4) running, with **Chroma Connect** installed and
  app/SDK control enabled for your devices. *Synapse must be running for the
  lights to change* — if it is closed, SDK calls silently succeed but nothing
  lights up.
- Python 3.9+ and `pip install requests`
- Claude Code

## Setup

### 1. Install

The server runs on **Windows** (needs access to the Chroma SDK); the hook script
runs in **WSL** (where Claude Code executes hooks).

**Windows** — in PowerShell:

```powershell
mkdir C:\razer-lights
copy razer_light_server.py C:\razer-lights\
pip install requests
```

**WSL** — in bash:

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

In an **Administrator** PowerShell:

```powershell
$action = New-ScheduledTaskAction `
    -Execute "C:\Python314\pythonw.exe" `
    -Argument "C:\razer-lights\razer_light_server.py" `
    -WorkingDirectory "C:\razer-lights"

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

Use the **full path** to `pythonw.exe` (adjust if your Python is installed elsewhere)
so the task doesn't depend on `PATH` being set correctly in the scheduler's environment.
The 10-second delay gives Razer Synapse time to start before the server tries to open
a Chroma session.

The `-Force` flag overwrites any existing `RazerLights` task, so this command is
safe to re-run whenever you change the action/trigger/settings above. Without it,
re-registering fails with a "file already exists" error (`HRESULT 0x800700b7`)
because a task with that name is already registered.

`pythonw.exe` runs without a console window. All diagnostics are written to
`razer_light_server.log` in the same directory as the script — check that file
if something isn't working.

If the server is already running when the trigger fires (e.g. after an unlock
rather than a full logout), the new instance detects the occupied port, logs a
message, and exits cleanly so the task doesn't show as failed.

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

## Notes

This project is not affiliated with or endorsed by Razer or Anthropic. "Razer",
"Chroma", "Synapse", "Viper", and "Kraken" are trademarks of Razer Inc.

## License

MIT — see [LICENSE](LICENSE).
