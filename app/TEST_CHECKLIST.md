# Test checklist additions for 0.9.6
- Settings shows 'Delete local videos after (days)' = 10. Set 0, save, set back.
- After 10 days a fully sent session's Game and Camera files disappear; older unsent ones stay.

# Test checklist additions for 0.9.5
- EVA Admin badge shows Connected; sign out of EVA Admin -> badge shows Sign in required.
- Press Sign in to EVA Admin during a recording -> refused with a clear message.
- In a review box, Sync failing -> Enter recipients manually -> review shows those emails, send works.

# Test checklist additions for 0.9.4
- Settings → Updates → Check for updates: with the source empty it reads GitHub iabbas05/EVA-RECORDING latest.json.
- Restart EVA: no Brave window appears. Press Sign in to EVA Admin: a window opens; after signing in it closes by itself and Check EVA connection passes.
- Close EVA: no brave.exe stays in Task Manager.

# EVA 0.9.1 — Streaming PC acceptance checklist

**Extra checks for 0.9.1 (do these first, takes ~10 minutes):**

| Check | Pass condition |
|---|---|
| Blank page | Settings → *Sign in to EVA Admin*: a Brave window opens straight on the EVA sign-in (never stays on about:blank). *Continue with Google* (if your EVA login uses it) opens normally. Close EVA and open it again: no second Brave window appears. |
| Browser | Settings → *Browser for EVA Admin*: Automatic opens Brave (if installed). *Sign in to EVA Admin* opens a Brave window; after signing in, Diagnostics shows "EVA Admin browser (Brave)" with its path. |
| Venue | Settings → EVA Admin: *EVA venues* lists your venue (Bahrain or Marassi). Click *Sign in to EVA Admin*, sign in, then *Check EVA connection*: message says "EVA Admin connected · <your venue>" and the sidebar shows the venue. |
| Saved sign-in | Close EVA completely and open it again (then restart Windows and open it again). Bookings load without signing in again. Settings → Diagnostics shows "EVA Admin saved sign-in: Saved securely". |
| Sign out | *Sign out of EVA Admin* → bookings stop loading and ask for sign-in; sign in again works. |
| Google test | *Test Drive & Gmail* → "Drive and Gmail work" and an email "EVA connection test" arrives in the sender's inbox; its link opens. |
| Partly linked players | Pick a booking that shows e.g. "1/10 attendees": EVA lists the linked player + owner and warns "1 of 10 players have an EVA account". |
| Restart while recording | Start a 1-minute test recording, close EVA, reopen within a minute: the session is still "Recording", Stop works, both files verify. |
| Non-Gmail recipient | Include one non-Google email in a test delivery: it receives one email and a Drive invitation, job ends "sent". |

---

# EVA 0.7.1 — Streaming PC acceptance checklist (original)

استخدم جلسة اختبار وإيميلين تملكهم. لا ترسل تسجيلات العملاء قبل نجاح الاختبار الكامل.

| Check | Pass condition |
|---|---|
| Update | Extract ZIP, close old EVA, run Install_EVA.bat. Installer reports EVA 0.7.1 verified. Settings shows 0.7.1. |
| Preserve data | Existing EVA Admin Chrome sign-in, Google credentials, recorder credentials, SQLite history and existing recordings remain available under the same Windows user. |
| One-time setup | Separate Game/Camera folders match actual recorder output folders. Save setup, close/reopen EVA; no setup repetition. |
| LAN | From Game Master PC, open the Streaming PC Settings LAN URL on TCP 8765; see EVA-JH. Refresh the page and control it again. |
| Actual sources | OBS Program shows the overhead camera; Streamlabs Program Projector shows the actual game. Disable Selective Recording. If projector is black/unsupported, configure the second OBS camera backend and verify both Program scenes. |
| Bookings before recording | In EVA Settings use Open EVA Admin, sign into Bahrain in English, then Verify. Choose the date and Load bookings. Select a slot; actual play times/game fill automatically and the linked recipient emails appear. Confirm against View attendees. Cancelled bookings must be excluded. No email is sent at this stage. |
| Start | Press Start once from Game Master. Both recorder applications visibly record; both EVA labels show REC. Pressing Start again cannot create another session. |
| Live previews | Move in the game and under the overhead camera; both small previews update. Enlarge/reduce each. Minimize/close the projector: EVA must show unavailable rather than a stale live image. |
| Timer | Record for at least 30 seconds; refresh Game Master page. Timer continues from the existing start and matches elapsed time. |
| Stop | Press Stop once. Both recorders stop. Two different playable files exist, with similar durations and correct content/audio. EVA shows ready for review. |
| Partial start | Prevent camera recorder connection, then press Start. EVA refuses or rolls back; it never reports a complete recording. Restore camera connection. |
| Active recorder failure | During a test recording, disconnect/close one recorder or pause OBS. EVA shows interrupted/incomplete and blocks sending even after reconnect. Stop/recover both before another session. |
| Missing file | Test an incorrect camera output folder. Stop must report failure rather than accept a single file. Restore the folder; use failed-session recovery if needed. |
| EVA Admin sync | Use a known Bahrain Arena 1 game slot with two bookings. Sync retrieves the correct game and all owners/attendees' valid emails together, excluding cancelled bookings and different games. Check against View attendees manually. |
| Mismatch protection | Wrong actual slot, different sessions in a similar slot, expired Admin sign-in, or unavailable attendee data must block sending. No guessed recipients. |
| Drive + Gmail | Select the two test emails and queue the session. Both videos appear on Drive with recipient-specific permissions; each test email receives one message containing Game and Camera links. Test opening both links with the recipient accounts. |
| Internet retry | After queue confirmation, disconnect internet during upload, then reconnect. Job persists and resumes/retries. Completed assets are reused and confirmed sent messages are not repeated. |
| Restart queue | Close/reopen EVA while a job is waiting for internet. Job remains in the queue and completes after internet returns. Closing EVA while actively recording or delivering is prevented. |
| Uncertain Gmail | If a send response is uncertain, EVA shows needs verification and stops automatic resend. Inspect Gmail Sent before any manual follow-up. |
| Auto repair | With no active session, close OBS/Streamlabs. EVA attempts to launch installed recorders and reconnect without changing saved credentials. For the second OBS camera backend, start the second instance and verify reconnection. |
| Update safety | During recording, Update & Restart is blocked. A trusted newer release applied while idle restarts EVA and retains settings/history. An invalid/tampered ZIP is rejected. Check retained backups. |
| Diagnostics | Settings → Diagnostics shows library, database, recorder, folder, Google and saved browser states. Staff operation does not expose a session ID or logs in the main dashboard. |

**Go-live condition:** both playable recordings, exact Admin recipients, private Drive access, and exactly one test email per recipient all pass on the actual Streaming PC and Game Master PC.

**Known commissioning dependencies:** Chrome, Edge WebView2 Runtime, recorder profiles/encoders and Remote Control settings, OAuth Desktop client/account permissions, Private LAN/firewall, actual Admin layout and pagination, and Streamlabs projector/GPU capture support.
