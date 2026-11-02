# EVA 0.9.8 — Streaming PC release

## What changed in 0.9.8
- **Choose what to record.** A selector next to *Record* on the dashboard: *Streamlabs + OBS (both)*, *Streamlabs only* or *OBS only*. It is remembered. Only the chosen recorder is started, checked and stopped; the other may be closed. A one-video session is verified, uploaded to Drive and emailed with just that video.
- **Settings is its own page** in the left menu. The dashboard shows only recording.
- **Faster EVA session lookup.** EVA reuses the bookings the background sync has just read instead of reloading the list, no longer waits on pages that have no venue button, and reads player accounts in several tabs at the same time.

## What changed in 0.9.7
- **Google OAuth file is found automatically.** Put the file you downloaded from Google in the config folder under any name (`client_secret_….json`, or a name Windows changed). EVA finds a Desktop-app client file by itself. A *Web application* file is rejected with a clear message.

## What changed in 0.9.6
- **Automatic cleanup of old videos.** Ten days after a session has been fully delivered (every email sent) and both videos are saved in Drive, EVA deletes the local Game and Camera files from the recording folders. Settings → *Delete local videos after (days)* changes the number; 0 turns it off. A recording is never cleaned if delivery is unfinished, a video is not confirmed in Drive, or the file is outside the two recording folders. The Drive copies are not touched. It runs hourly, never during a recording or update.


## What changed in 0.9.5
- **EVA Admin health.** The EVA Admin badge now says one of: Connected, Sign in required, EVA Admin changed, Reconnecting.
- **Layout check.** If EVA changes the Admin page (columns, dates, play times, booking table), matching pauses with a clear message instead of guessing. Recording and delivery keep working.
- **Manual recipients.** When matching fails, the review box offers *Enter recipients manually* (typed emails, 1–10). They are marked MANUAL and go through the normal review and send steps. EVA Admin is not used for that recording.
- **Sign-in at a safe time.** *Sign in to EVA Admin* is refused while a recording is running, so a window cannot disturb a session. The saved sign-in keeps refreshing in the background.

## What changed in 0.9.4
- **Updates come from GitHub.** Settings → Updates → *Check for updates* reads `latest.json` from https://github.com/iabbas05/EVA-RECORDING (main branch), downloads the release ZIP it names and installs it only if the SHA-256 matches. Leave *Update source* empty to use GitHub.
- **No Brave window for the EVA Admin connection.** The connection runs hidden. A Brave window opens only when you press *Sign in to EVA Admin*, and closes by itself once you are signed in. The hidden browser is closed when EVA closes.

### Publishing a release (maintainer)
1. Create a GitHub Release tagged `v<version>` and attach `EVA_v<version>_UPDATE.zip`.
2. Run `python make_latest_json.py EVA_v<version>_UPDATE.zip .` and commit the `latest.json` it writes to `main`.

## What changed in 0.9.3
- **Brave no longer opens a blank page.** EVA now starts Brave as a normal program and gives it the EVA Admin address itself, then attaches to it. Before, EVA opened Brave and navigated it afterwards, which could leave the window on `about:blank`.
- **Google / Facebook sign-in works in that window.** Brave is no longer started with the automation flags that made those sign-ins refuse to continue.
- **The Admin window can stay open when EVA restarts**, and EVA re-attaches to it instead of opening a second one.
- **Automatic mode falls back to Google Chrome** if Brave cannot be started. Forcing Brave in Settings never falls back.
- **Nothing reopens by itself.** After a failed browser start the background booking sync waits 10 minutes instead of retrying every minute (pressing *Sign in to EVA Admin* always tries again), a browser that does not become ready is closed instead of left as a blank window, and *Auto Repair* starts OBS or Streamlabs at most once per 10 minutes (the *Reconnect* button always works).
- If Brave closes immediately (another window is already using EVA's profile) EVA now says so in plain words.

# EVA 0.9.2 — Streaming PC release

## What changed in 0.9.2
- **Brave support.** EVA Admin opens in Brave when it is installed (Google Chrome otherwise). Settings → *Browser for EVA Admin* can force Brave or Chrome. Brave keeps its own sign-in profile, separate from Chrome's, so after switching browsers sign in to EVA Admin once more. Requirements below that say "Google Chrome" now mean Brave or Chrome.

## What changed in 0.9.1 (on top of 0.9.0)

- **EVA Admin now matches the live site.** Checked against the real Admin pages: the venue is read from the "Scope" menu (Marassi) or a venue drop-down (Bahrain); booking pages are recognised by their booking row (the page title is "Order#…"); the attendee pop-up is found by its title in any letter case.
- **Two venues.** Settings → *EVA venues this PC may use* (default "Bahrain, Marassi") and *EVA arena number(s)*. EVA shows the detected venue in the sidebar and in emails ("EVA Marassi"). Anything else in EVA Admin is still refused.
- **Players without an EVA account no longer block sending.** A booking showing "1/10 attendees" now sends to the linked players plus the booking owner, and EVA warns "1 of 10 players have an EVA account". Turn on *Block sending unless every player has an EVA account* in Settings to get the old strict behaviour.
- **Sign in to EVA Admin once.** The sign-in cookies are saved with Windows protection (this Windows user only) and put back after EVA or Windows restarts, only where Chrome has lost them. Settings → *Sign out of EVA Admin* removes the saved sign-in and the browser profile.
- **Restart-proof recording.** If EVA is closed or crashes while both recorders keep recording, EVA re-attaches to the same session (up to 90 seconds for the recorders to reconnect). Stop still verifies both files against the real start time. If a recorder stopped meanwhile the session is marked incomplete as before.
- **Drive sharing.** Each share is confirmed in Drive before the email is sent. Recipients without a Google account receive Drive's own invitation instead of failing the job.
- **Settings → Test Drive & Gmail.** Uploads a tiny file and emails its link to the signed-in Google account only. Proves Google works before any customer is involved.

Install: run START_HERE.bat (ALL_IN_ONE), or Settings → Browse update ZIP → Update & Restart (from 0.9.0 onwards).

---

# EVA 0.7.1 (earlier notes, still accurate for recorder and delivery setup)

EVA is a Windows 10/11 x64 desktop app with one Game Master LAN control page, fixed pairing identifier **EVA-JH**, separate Game and Camera recordings, verified session recipients, and durable Google Drive/Gmail delivery.

## Install or update

1. Stop recording and close the existing EVA app.
2. Extract **EVA_v0.7.1_UPDATE.zip** completely to a normal folder.
3. Double-click **Install_EVA.bat**. Install under the same Windows account used by the existing EVA installation.
4. Open the **EVA** desktop shortcut. The installer checks the packaged executable's embedded version, dependencies, each applied file checksum, and an isolated HTTP startup before reporting success.

No Python installation or build is required on the Streaming PC. Microsoft Edge WebView2 Runtime and Google Chrome must be available. OBS Studio and Streamlabs Desktop are external recorder applications and keep their own existing profiles and encoder settings.

Existing installation/data location: `%LOCALAPPDATA%\EVARecordingManager`. The updater replaces only listed application files. It preserves recordings, SQLite data, Google OAuth configuration, EVA's saved Chrome profile, and existing Windows Credential Manager entries. The same Windows user must run EVA to access those entries.

## One-time setup on Streaming PC

Open **Settings**. Existing saved OBS and Streamlabs credentials continue to use their original Credential Manager names. Leave password/token fields blank to keep them.

- This tailored Install_EVA.bat selects **Streamlabs Game / OBS Camera** after verified installation. Existing folder paths, credentials, Google config, database, and browser profile are preserved. Settings before the layout change are backed up. The main OBS connection on port 4455 controls the camera; a second OBS instance/port 4456 is not used in this layout.
- OBS Program must show the overhead camera. Enable its authenticated WebSocket and match EVA's OBS port/password. Streamlabs must show and record the game; keep its existing Remote Control token.
- Configure **different existing folders** in OBS and Streamlabs, and enter the same two folders in EVA Settings. Use unique recording filenames. Turn off automatic file splitting and automatic remux that adds another file to the capture folder. MKV is recommended for recovery after recorder crashes.
- Open Streamlabs's **Program Projector** window, save its exact title, and keep the window open and unminimized. Disable Selective Recording and use the same scene for recording and the projector. EVA captures the projector's client area and rejects missing/black capture; it does not label a captured editor window as the recorded game image.
- Some Streamlabs/GPU versions do not expose the projector's pixels to Windows capture. EVA reports the preview as unavailable; validate the Game preview on the actual PC. The optional **OBS Game** layout still supports a second camera OBS, using a separate portable installation/configuration directory and port 4456. Separate profiles alone do not isolate all shared settings.
- Click **Open EVA Admin**, sign in manually, and retain EVA's own Chrome profile. EVA never requests your EVA Admin password.
- Use **Open Google OAuth folder**, add your existing Google Desktop OAuth client JSON as `google_oauth_client.json`, then sign in to Google if not already signed in. Enable Drive API and Gmail API in that Google project. The app uses `drive.file` and `gmail.send`.
- On Game Master PC, bookmark the LAN URL displayed in Settings, such as `http://<Streaming-PC-IP>:8765`. Windows network profile must be Private; allow TCP 8765 from LocalSubnet. The installer attempts the firewall rule; Windows administrator rights may be needed for that one step.

Setup is persistent. Provider-expired sign-ins or changed recorder passwords still require a one-time refresh. The application cannot bypass an account provider's expiry/revocation policy.

## Daily operation

1. On Game Master PC, enter the **actual game slot**, including date/start/end. Game name is optional and is filled from verified EVA bookings where available.
2. Click **Start**. Both recorders must positively confirm recording. If either fails, EVA attempts to stop both. A stop that cannot be confirmed remains an active recovery problem.
3. Observe **REC**, the timer, and both previews. The timer uses the saved server-side start time and survives page refresh. Missing previews show a failure instead of leaving a stale image labelled live.
4. Click **Stop**. Both recorders must confirm idle. EVA associates the two new session files with their configured capture folders and verifies real video streams, first/end frames, and duration agreement. Files are not manually chosen in the daily send workflow.
5. Click **Sync & review**. Review all recipient emails from all bookings belonging to that actual game/session, then add both files to the delivery queue.
6. Monitor **upload Game → upload Camera → private sharing → email → sent**. The queue persists across app restarts and retries safe failures with increasing delays after an internet outage. Confirmed sent messages are not repeated.

If a recorder disconnects, pauses, or stops outside EVA, the session is latched incomplete. Reconnection never silently restores it to complete. A failed session can be explicitly closed after both recorders confirm Stop; its automatic sending stays blocked.

Gmail may accept a message just before the connection fails. Such jobs show **needs verification** and do not resend automatically. Check Gmail Sent before any manual follow-up. A session's recipient confirmation is preserved in its durable job; retrying never adds newly discovered recipients.

## EVA Admin synchronization

The existing project uses the operator-authenticated `admin.eva.gg` website through Chrome/Playwright, not an official EVA API. The connector supports the observed English Admin combined Session column, month/day dates, data-booking row navigation, date presets and guarded pagination. The daily picker groups only non-cancelled Bahrain bookings by actual play time/game. It reads the bookings table, follows only matching booking detail links, opens View attendees, and visits only the linked user detail pages to read their email fields. It checks branch, arena, actual game times, official session identities when available, cancellation states, and distinct games. It blocks partial/unread attendee lists, missing details, changed session metadata, and conflicting session/game matches.

This connector depends on the actual EVA Admin site's table labels/navigation and the accessible booking page. It is deliberately conservative: a changed layout, pagination that hides the desired slot, or expired login blocks sending. It does not guess a booking or silently accept a partial recipient group. Authenticated synchronization with the branch account must pass the supplied Streaming PC checklist before customer use.

## Updates and rollback

**Update & Restart** accepts a newer local EVA release ZIP or the update manifest (GitHub `latest.json` by default, or a custom HTTPS source in Settings). The manifest has `version`, `package_url`, and `sha256`. The default source is the GitHub repository iabbas05/EVA-RECORDING.

Updates are blocked during recording or an in-progress delivery. Queued jobs remain persisted. The installer rejects unexpected paths, duplicate entries, mismatched checksums, missing required files, executable/resource version mismatches, and failed startup. App-file backups are retained under `updates\backups`. An offline copy of the database/settings is also retained there; credentials remain in Windows Credential Manager. The current updater keeps the old app files if validation fails and restores replaced files on application failure. Backups are not deleted on success.

Release archives are unsigned. The SHA-256 manifest verifies integrity; use only release files from a trusted source. Online releases come from the GitHub repository above (or the HTTPS host you configure).

## Diagnostics and verification scope

Diagnostics and logs are under Settings. Local logs rotate in `logs\eva.log`. This release is verified by Python/JavaScript/PowerShell syntax checks, isolated migration/workflow/media/delivery tests, the real HTTP handler, desktop/mobile UI checks, packaged executable self-tests, and isolated installation/update/rollback checks.

OBS/Streamlabs devices, real camera/game sources, GPU/projector behavior, Game Master firewall access, EVA Admin authenticated DOM, and real Google account delivery need the on-site commissioning checklist. This package is a release candidate until those real-machine checks pass.
