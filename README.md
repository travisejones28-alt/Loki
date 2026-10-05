# Loki

Loki is a visual desktop assistant with **Monitor Mode** and **Offline Automation Mode**.
Both modes share the same region capture, template/context detector and appearance latch.
Every launch starts in Monitor Mode with automated input disarmed.

For **World of Warcraft / VoidLink Run WHO**, use Monitor Mode only: Loki confirms the
button across several frames, sounds once, highlights it and logs the match. **You manually
move and click.** Monitor Mode has no input controller or route into the automation service.
WoW profiles are Monitor Only and automated actions against identified WoW windows are blocked.

Offline Automation is for explicitly permitted local applications. It supports alert only,
cursor movement, or movement plus left-click, with separate explicit arming, target binding,
dry run and fail-closed checks. Loki uses ordinary Windows APIs, no game memory, injection,
keyboard synthesis, drivers, concealment, anti-cheat interference or network manipulation.
Battle.net running by itself does **not** block a permitted target.

## Install on Windows

1. Download [Loki-Windows.zip](https://github.com/travisejones28-alt/Loki/releases/latest/download/Loki-Windows.zip)
   from the [latest successful build](https://github.com/travisejones28-alt/Loki/releases/latest).
2. **Extract the entire ZIP** into a permanent folder, such as `C:\Loki`.
3. Open `Loki\Loki.exe`. Keep its `_internal` folder beside the executable.

The executable is built on Windows by GitHub Actions after source tests pass. The download
link tracks the latest tested release. If no release is
available, check [Actions](https://github.com/travisejones28-alt/Loki/actions), or build locally
with the command below. Python is not needed to run the extracted Windows package.

Primary target: Windows 11, 64-bit. Windows 10 64-bit with a recent supported desktop
environment is also a target. Use WoW in **windowed or borderless windowed mode** so ordinary
desktop capture and the highlight can see it. Exclusive fullscreen can prevent both.

## First launch and calibration

1. Open the **VoidLink WHO Request** popup in WoW. Leave **Run WHO** visible.
2. Open Loki and select **Calibrate**.
3. Choose the monitor containing WoW. No pixel coordinates need to be typed.
4. Select **Select area and target**. Loki hides its windows for 0.9 seconds,
   then shows a frozen image of that monitor. Keep the pointer off the button during capture.
5. Drag a small search rectangle containing the popup. It can include enough surrounding
   space for the popup to move slightly. Press Escape to cancel.
6. Drag a rectangle around the **complete Run WHO button**, including its text and edges.
   Select the unhovered button, with no tooltip covering it.
7. Review the template preview. Select **Test live detection** while the popup remains open.
   The result should say **MATCH**, with confidence near 1.0.
8. Select **Save calibration**, then **Start**.

The monitor is captured once in full during calibration. Routine monitoring captures only
the selected search area. Loki saves the selected button and a six-pixel surrounding patch
as templates; it does not keep the full calibration screenshot.

Keep Loki's main window, test window, and other desktop windows outside the capture area.
Screen capture sees visible pixels; a covered or minimized WoW window cannot be recognized.

## Monitoring and the tray

- **Start** begins capture; **Stop** stops it and removes the highlight.
- Default confirmation is **4 matching frames at 10 checks/second**. The first alert normally
  follows about 300–400 ms of visibility, plus capture and processing time.
- The sound plays once. The same highlight window is reused while the popup remains visible.
- The highlight is click-through and does not take focus. Its border sits outside the
  template and context area, leaving the button unobstructed.
- Five consecutive missing frames re-arm the detector. Brief hover or brightness changes
  use a lower presence threshold to avoid repeated alerts.
- Close the main window to leave Loki in the tray. Gray indicates stopped, green indicates
  monitoring, and amber indicates diagnostic testing.
- The tray menu offers **Open Loki**, **Start Monitoring**, **Stop Monitoring**, **Calibrate**,
  **Test Detection**, and **Exit**. Only Exit closes the tray application.
- **Ctrl+Shift+F11** immediately stops monitoring. Settings offers F6 through F11. Loki reports
  if Windows cannot register the chosen shortcut; use Stop or choose another shortcut then.

Loki allows one running instance per data folder, preventing duplicate notifications.

## Test Detection and settings

**Test Detection** stops normal monitoring and opens a live preview of the search region.
It displays the confidence, popup-context score, match state, and bounding box. Green boxes
are matches; amber boxes are the best candidate below the required threshold. This mode
does not play notification sounds, create a target overlay, or save debug screenshots.
Closing it leaves monitoring stopped; select Start when ready.

**Settings** provides confidence, 5–15 checks/second, confirmation/disappearance frame
counts, sound, highlight, debug screenshots, emergency hotkey, and startup options. Settings
and calibration pause monitoring. The **Test sound** button lets you check the laptop's
actual audio output. Four confirmation frames are a good starting point.

**Start Loki with Windows** uses the normal current-user Windows `Run` registry entry named
`Loki`. It launches in the tray and starts **passive Monitor Mode** using saved calibration.
It cannot select Offline Automation or arm input. No administrator
rights or service are needed. Turn it off to remove that entry. Set it again if you move the
application folder. **Begin monitoring when I open Loki** independently controls manual launches.

## Profiles and Offline Automation

1. Select **New Profile**, name the permitted local application, then **Profile / Target**.
2. Select **Refresh running windows**, choose its executable/PID/window, and **Bind selected
   running window**. Loki reads ordinary window and process identity metadata. It saves the
   full executable path, filename, PID, creation identity, window handle/class/title and bounds.
3. Choose **Alert only**, **Move cursor to target**, or **Move cursor and left-click**.
   Set **Action delay** between 0 and 5000 ms. Keep **Dry Run** enabled for initial testing.
4. Save, calibrate that profile’s target and context, and use Test Detection to check matching.
5. Explicitly choose **Offline Automation**. The UI shows **DISARMED**. Select **ARM
   AUTOMATION**, return to the bound application, and cause a fresh target appearance.
6. Dry Run performs safety/delay/fresh-image checks and logs `DRY RUN WOULD CLICK x=… y=…`
   (or MOVE). It never constructs or calls the Windows input backend. Inspect the log before
   disabling Dry Run in Profile / Target and explicitly arming again.

Alert only never creates an input backend. Live movement/click acts once at the **center of
that confirmed target**, within the monitor, capture area and bound window’s client bounds.
An independent hard input limiter allows at most one action per second and 30 per minute.
The cursor remains free; manual input or substantial movement cancels delayed actions.

The exact bound window/process must still exist and be foreground. Restarting the target,
changing its handle, title (unless an explicit wildcard is configured), bounds, executable,
monitor or DPI blocks/disarms automation: rebind and check calibration. Covered targets are
blocked. Missing/unverifiable metadata fails closed. The same detector takes a fresh ROI
image immediately before dispatch; disappeared or moved targets are cancelled.

**Alt-Tab to WoW blocks automation and cancels pending actions**, while Loki keeps running.
A desktop foreground-change event counter also invalidates away/back transitions between
capture frames. Returning to a permitted window never executes the old action: the latched
appearance must disappear and a fresh confirmed appearance must occur. Built-in protection
checks WoW retail/classic/test executable names, installation-path components, window titles,
classes and executable version metadata. Profile / Target → Protected applications lets you
add wildcard executable/title/full-path rules, without removing built-in WoW protections.
A WoW-bound/named profile is forced Monitor Only; arming controls are unavailable.

Stop, emergency stop (**Ctrl+Shift+F11**, configurable F6–F11), mode/profile changes, opening
calibration/settings/target binding or diagnostic testing disarm and cancel pending actions.
Queued capture reports are invalidated on stop. No armed state is saved, and no CLI option
arms automation. Startup flags always mean passive monitoring.

The UI and tray tooltip explicitly identify Monitor, diagnostic, Offline Automation
Disarmed/Armed and blocked states. Offline tray colors are amber (disarmed), purple (armed)
and coral (blocked); read the text as well. Selecting Start in Offline Mode starts capture
only and never arms input. An alert/highlight can still occur while input is disarmed/blocked.

Windows `SendInput` is global: the final foreground/protected-window check and dispatch are
not an atomic OS operation. Loki repeats checks directly before dispatch and cancels observed
focus transitions, but no ordinary user-space implementation can promise zero race time
against a simultaneous foreground switch or identify every adversarially altered executable.
**Use Monitor Mode whenever WoW is in use**; its structural absence of input generation is
the unconditional passive guarantee. No anti-cheat detection or evasion is attempted.

## Configuration, logs, and screenshots

All user data stays locally in `%LOCALAPPDATA%\Loki`:

| Location | Contents |
| --- | --- |
| `config.json` | Readable settings, physical search coordinates, monitor identity, DPI, templates |
| `templates\` | Each profile’s button and context PNGs |
| `logs\loki.log` | Startup, calibration, capture, candidate, confirmed detection, loss and errors |
| `debug\` | Optional annotated detection screenshots; newest 20 only |

Settings has **Open data folder**. Configuration saves are atomic. Schema 2 stores named profiles with independent calibration,
thresholds, FPS, notifications, mode/action/delay/dry-run settings and target binding. Global
startup/hotkey settings and extra denylist rules stay at the workspace level. Schema 1 is
migrated automatically to **WoW - VoidLink WHO / MONITOR ONLY** with all existing settings
and image paths preserved, without recalibration. A byte-identical `config.v1.backup.json`
is saved before migration. No arming flag exists in the file. Unreadable configurations
are preserved as `config.corrupt-*.json` (newest three) and monitoring requires fresh calibration.
Logs rotate at approximately 512 KB, with three backups. Profile/mode/arming, protected foreground, pending/cancelled actions, rate limits, dry runs
and emergency stop are also logged. Regular frames and confidence scores
are displayed but not written to disk; coordinates and confidence are logged on important events.
Debug screenshots are disabled by default and saved only on confirmed appearances.

The JSON also contains advanced `appearance_threshold`, `context_threshold`, and
`release_threshold` settings. Defaults check both button correlation and pixel appearance,
then validate popup context. Recalibrate before lowering confidence. The release threshold
must not exceed the trigger threshold. Search areas are limited to two million pixels to
keep capture and processing bounded.

## DPI and multiple monitors

Capture and configuration use physical pixels. Calibration gestures and the highlight
convert coordinates relative to the chosen monitor, including negative desktop origins
and different DPI scales. The application requests Per-Monitor V2 DPI awareness. Display
resolution, position, or scaling changes stop monitoring with a request to recalibrate;
disconnecting the selected monitor does the same. Changing WoW UI scale, button artwork,
or popup position outside the search area also calls for recalibration.

## Build from source

Clone this repository with GitHub Desktop. Install **Python 3.12, 64-bit**, with the Python
launcher. In the repository folder, run one PowerShell command:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The script creates `.venv`, installs pinned dependencies, generates the bundled icon and
WAV, runs the tests and source checks, builds with PyInstaller, then opens and closes the
packaged GUI as a smoke test. Results:

```text
dist\Loki\Loki.exe
dist\Loki-Windows.zip
```

Distribute the ZIP with the whole folder. This is a one-folder build for predictable launch
and fewer extraction steps; copying only `Loki.exe` is insufficient. Builds are ordinary
unsigned desktop applications. PyInstaller is configured without UPX compression.

To run and test the source directly:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m loki
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

Useful options: `--tray`, `--monitor`, `--data-dir "C:\Loki-TestData"`, `--version`, and
`--smoke-test`. `--smoke-test` checks GUI initialization and clean exit without capturing.
The test suite uses synthetic images, fake desktop metadata, fake input senders and an
offscreen Qt UI. It does not require WoW and **never moves/clicks a CI runner mouse**.

GitHub Actions tests Linux and Windows, builds and smoke-tests the executable on Windows,
uploads `Loki-Windows.zip`, then publishes the successful main-branch build as a release.
Failed tests or builds do not publish a release. The permanent download link tracks the
latest published build. Linux can run source/unit/GUI tests and validate a native package;
it cannot cross-compile `Loki.exe`.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| No match | Keep the popup visible, move other windows away, and inspect Test Detection. Recalibrate after UI changes. |
| Calibration captures Loki | Wait for the capture countdown and place Loki outside the game area. |
| Repeated alerts | Make a clean unhovered template and include the button edges; inspect confidence while hovering. Increase missing-frame count if there is intermittent occlusion. |
| No sound | Use Settings → Test sound. Check the Windows volume mixer and output device. |
| Highlight absent | Enable highlight; use windowed/borderless WoW. Check logs for an overlay error. |
| Display changed/disconnected | Reconnect and recalibrate. Loki deliberately avoids using stale coordinates. |
| Template missing | Select Calibrate again; no default image can match every user's UI scale. |
| Loki already running | Open the L icon in the Windows tray. |
| Start with Windows stopped working | Keep the extracted folder in a permanent location and toggle the startup setting again. |
| Build fails | Use Python 3.12 64-bit and allow pip to download requirements; read the first failing test/build message. |

## Validation limits

Automated tests cover visual thresholds, popup context, latching, consecutive-frame
confirmation/disappearance, persisted calibration, DPI conversion at 100/125/150%, errors,
diagnostic isolation, GUI selection, notification routing, and bounded logs/screenshots.
Packaged smoke tests verify launch and clean exit. A live Windows/WoW check is still needed
to confirm your actual button image, mixed-monitor placement, click-through behavior and
speaker output. Offline input must be manually validated against a harmless local test
application after dry-run validation; automated tests inject fakes. Synthetic/offscreen checks cannot certify those hardware conditions.

## Implementation references

- [Qt high-DPI coordinate model](https://doc.qt.io/qt-6/highdpi.html)
- [OpenCV template matching](https://docs.opencv.org/4.x/df/dfb/group__imgproc__object.html)
- [Windows RegisterHotKey](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey)
- [MSS](https://github.com/BoboTiG/python-mss)

