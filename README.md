# SpeedTimer

SpeedTimer is a small Windows desktop widget that counts down the real time needed to finish a video at a chosen playback speed. It can also use Windows media-session information to follow playback, show the current media name, and adjust its remaining-time estimate after a seek or skip when the app provides that information.

## Features

- Enter a video length and playback speed, then see the estimated finish time.
- Pause and resume from the widget or with **Ctrl+Alt+Space**.
- Optionally sync pause/resume with supported Windows media sessions.
- Read the current media title, position, duration, and playback rate when the media app exposes them to Windows.
- Follow selected media sources and switch to the most recently started followed source.
- Drag the widget or choose a screen-position preset.
- Save a startup position preset or the widget's current dragged location.
- Choose overall widget size, independent widget length, and background transparency.
- Keep the widget behind fullscreen windows by default, or enable **Keep above fullscreen windows** in Settings.

## Requirements

- Windows 10 or Windows 11.
- Python for Windows, including Tkinter. The official installer is available from [Python.org](https://www.python.org/downloads/windows/).
- Internet access on first launch if the Windows media helper packages are not already installed. The timer can still be used manually if media detection is unavailable.

## Installation

1. Download or clone this repository and extract it if necessary.
2. Keep `SpeedTimer.pyw` in a folder of your choice.
3. Double-click `SpeedTimer.pyw` to start the widget.

If double-clicking does not start it, open PowerShell in the folder containing the file and run:

```powershell
pyw .\SpeedTimer.pyw
```

SpeedTimer attempts to install its media integration packages automatically the first time it starts. If that does not work, install them manually:

```powershell
py -m pip install winrt-runtime winrt-Windows.Media.Control winrt-Windows.Foundation winrt-Windows.Foundation.Collections
```

Restart SpeedTimer after installing the packages.

## Start SpeedTimer when you sign in to Windows

SpeedTimer does not add itself to Windows startup automatically. To launch it at sign-in:

1. In File Explorer, find `SpeedTimer.pyw` and create a shortcut to it (on Windows 11, use **Show more options → Create shortcut** if needed).
2. Press **Windows+R**, enter `shell:startup`, and press Enter. This opens the Startup folder for your Windows account.
3. Move or copy the SpeedTimer shortcut into that folder.
4. Sign out and sign back in, or restart Windows, to check that SpeedTimer opens.

To stop it launching at sign-in, remove the shortcut from the Startup folder. This is separate from **Settings → Startup position**, which only controls where the widget appears when it launches.

## Set a timer

1. Right-click the widget and choose **Set timer…**, or click the pencil icon.
2. Enter the video's original length in hours, minutes, and seconds.
3. Enter the playback speed, such as `1.5` or `2`.
4. Click **Start**.

SpeedTimer calculates the viewing time as:

```text
viewing time = video length ÷ playback speed
```

For example, a 90-minute video at 1.5× takes about 60 minutes to watch. The estimated finish time uses the computer's local clock.

## Controls

| Action | How |
| --- | --- |
| Pause or resume | Click the ring or press **Ctrl+Alt+Space** |
| Reset the countdown | Click the reset arrow |
| Edit length or speed | Click the pencil, double-click an empty part of the pill, or right-click and choose **Set timer…** |
| Open settings | Right-click the widget |
| Move the widget | Drag an empty part of the pill |
| Change overall size | Use **Settings → Widget size**, or hold Ctrl and scroll over the widget |

## Settings

Right-click the widget and open **Settings**.

- **Sync with media (auto pause/resume):** Pause the timer when a followed session pauses and resume it when playback starts. When available, SpeedTimer can also send play/pause commands to the media app.
- **Use media position for remaining time:** Recalculate the estimate from Windows' current playback position. This can correct the timer after seeking or skipping.
- **Media sources to follow:** Choose which Windows media sessions affect the timer. Music apps such as Spotify are ignored by default; this can be changed from the source list.
- **Move widget to:** Move it immediately to a screen-position preset.
- **Startup position:** Choose the position preset to use the next time SpeedTimer starts, or save the current dragged location. This saves the widget's position; it does not launch SpeedTimer automatically when Windows starts.
- **Widget size:** Scale the whole widget from 60% to 250%.
- **Widget length:** Set the width independently, from 240 px to 420 px.
- **Transparency:** Change the pill background from 10% to 100% opacity. Text and icons remain opaque.
- **Keep above fullscreen windows:** Keep the widget above fullscreen content when enabled. By default, it yields to fullscreen windows and returns over ordinary windows afterward.

## Media detection notes

SpeedTimer uses the Windows Global System Media Transport Controls session API. The media app must publish a Windows media session for SpeedTimer to see it. Some browser sites and desktop apps do not publish a session, or publish only part of the information.

- Position and duration are available only when the app reports timeline data.
- Automatic playback-speed detection works only when the app reports its playback rate. Browser extensions may change video speed without updating Windows; if a browser session reports 1×, SpeedTimer uses the speed entered in **Set timer…**. Enter the same speed you selected in the extension.
- Title, playback state, and timeline data can differ between apps. If media detection is unavailable, use the timer manually; the timer itself does not require the media packages.
- When multiple followed sources are playing, SpeedTimer follows the one that started most recently. If more than one source plays at once, the chosen source may not match the video you are actively watching.

## Configuration

Settings are stored in:

```text
%APPDATA%\SpeedTimer\config.json
```

SpeedTimer creates this file automatically on each user's PC to remember widget placement and appearance, saved timer length and speed, media-sync preferences, and followed sources. It is outside the project folder and does **not** need to be uploaded to GitHub.

For the current single-file app, the repository needs `SpeedTimer.pyw` and this `README.md`. The media helper packages are installed automatically on first run, or can be installed manually using the command above.

## Troubleshooting

### The widget opens paused

Click the ring or press **Ctrl+Alt+Space**. If media sync is enabled, playback state from Windows can pause or resume the timer. Turn off **Sync with media** if you want to control the timer independently.

### Media detection is unavailable

Install the packages listed in [Installation](#installation), restart SpeedTimer, and make sure the video app is publishing a Windows media session. You can still enter the length and speed and use the timer manually.

### The widget is hidden during fullscreen playback

This is the default behavior. Exit fullscreen to see the widget again, or enable **Settings → Keep above fullscreen windows**.
