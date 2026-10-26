# Spike: SoundCloud sign-in in a real Edge window

This spike checks two things. First, can someone sign in to SoundCloud with Google in a real Edge window that sc-mp3 opens on a new temporary profile? Second, can sc-mp3 then read the `oauth_token` and `oauth_refresh_token` cookies through the Chrome DevTools Protocol? T-003 (`spikes/signin/README.md`) found that Google can't work in the app's own pywebview window.

Tested on 2026-10-25 on Windows 11 with Edge 156.0.4314.42, websockets 17.2, yt-dlp 2026.08.19 and Python 3.13. The account was a free SoundCloud account that signs in with Google.

## Short answer

- **Edge with a DevTools port:** Google refuses the sign-in. With `--remote-debugging-port`, `navigator.webdriver` is true on every page, and Google showed "This browser or app may not be secure" right after the email step.
- **Two steps:** Google allows it. The person signs in with Google in an Edge that has no port, and closes the window. sc-mp3 then starts a headless Edge on the same profile and reads both cookies through DevTools. `/me` and one renewal worked with those tokens.

## Running it

The script is Windows only, since it finds Edge in the registry and lists processes with PowerShell.

| Command | What it does |
|---|---|
| `uv run --locked python spikes/browser-signin/browser_signin.py signin --scratch <folder>` | The one-step run. Opens `https://soundcloud.com/signin` in a new Edge with a DevTools port, on a temporary profile under `<folder>`. It polls the cookies once a second for up to 10 minutes (`--timeout`). Once both cookies appear, it closes Edge, deletes the profile, and runs `/me` and one renewal through `core.auth`. |
| `uv run --locked python spikes/browser-signin/browser_signin.py twostep --scratch <folder>` | The two-step run. Step 1 opens the same page in an Edge with no port and waits up to 10 minutes for the person to close it. Step 2 starts Edge headless on the same profile, reads the two cookies once, closes it and deletes the profile. Then come the same `/me` and renewal checks. |
| `uv run --locked python spikes/browser-signin/browser_signin.py selftest --scratch <folder>` | The one-step start, with no account. It checks the port, polls the signed-out page 5 times, then closes the window with `WM_CLOSE`, as clicking its X does, to see that the close is noticed. |

Keep `<folder>` outside the repo. The default is `scmp3-browser-signin` in the system temp folder.

How the tokens are handled:

- They stay in memory. Nothing writes them to a file.
- Output shows only a cookie's length and first 4 characters.
- Each value is registered with `core.log.register_secret` as soon as it's read, and every printed line goes through `core.log.redact`.
- The headless read keeps only the two cookies. Every other cookie, Google's included, is dropped in memory without being printed.
- The `/me` and renewal checks run in a temporary `SCMP3_HOME` with an in-memory keyring. That folder is deleted afterwards, so nothing reaches Credential Manager or real sc-mp3 settings.

## Files

| File | Role |
|---|---|
| `browser_signin.py` | Command line, the one-step run and its polling, and the printed results |
| `two_step.py` | The two-step run |
| `edge.py` | Finds `msedge.exe`, starts and stops it by process id, lists its processes, checks the port's listeners, deletes the profile |
| `devtools.py` | A small client for the browser's own DevTools WebSocket (`websockets.sync.client`) |
| `cookies.py` | Picks the two cookies and describes them without values. Also gives pages as scheme, host and path only |
| `account.py` | `/me` and one renewal through `core.auth`, in a temporary `SCMP3_HOME` |

## How Edge is found and started

Finding Edge:
- First the `App Paths\msedge.exe` key, under HKCU and then HKLM. On this machine it's under HKLM only, pointing at `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`.
- Then `Microsoft\Edge\Application\msedge.exe` under `ProgramFiles(x86)`, `ProgramFiles` and `LOCALAPPDATA`.

Chrome would be found the same way, but this wasn't tested:
- On Windows, `App Paths\chrome.exe`, then `Google\Chrome\Application\chrome.exe` under the same three folders.
- On macOS, `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`, then the same under `~/Applications`. Edge is at `/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge`.
- On Linux, `google-chrome`, `google-chrome-stable`, `chromium`, `chromium-browser`, `microsoft-edge` and `microsoft-edge-stable` on `PATH`. A Snap Chromium has its own private `/tmp`, so the profile and `DevToolsActivePort` must live outside `/tmp` there.

Every start uses a new `--user-data-dir` under the scratch folder, plus `--no-first-run`, `--no-default-browser-check` and `--disable-sync`.
- The one-step run adds `--remote-debugging-port=0`. Port 0 lets Edge pick a free port. Edge writes the port, and the browser target's path, to `DevToolsActivePort` in the profile, 0.2 to 0.4 s after the start.
- Two-step: step 1 adds nothing. Step 2 adds `--headless=new --remote-debugging-port=0` and opens `about:blank`, so no SoundCloud page loads and nothing on SoundCloud's side can renew the tokens.
- Nothing passes `--enable-automation`. Nothing attaches to a sign-in page or enables `Runtime` on it. The runs call only `Storage.getCookies`, `Target.getTargets` and `Browser.close` on the browser target.

`--disable-sync` isn't in the ticket. Without it, Edge signs a new profile in to the Windows Microsoft account and turns sync on. It showed `edge://sync-confirmation-dialog/` with "We are now syncing your browsing data across all your devices". Sync would copy the person's Edge data into the temporary profile, and send its pages to their Edge history. With the flag, the profile still holds the Windows account's sign-in (one entry in `account_info`), but no sync data and no dialog. A command-line flag can't turn off that sign-in itself. Only the Edge policies `BrowserSignin` and `ImplicitSignInEnabled` can, and those would change the person's own Edge too.

The sync dialog is modal, so while it was open `WM_CLOSE` didn't close the window.

Other flags that were tried, in probes with no account:
- `--inprivate`: the page's cookies aren't in the default context, and `Storage.getCookies` with the page's `browserContextId` fails with "Failed to find browser context". So DevTools can't read them.
- `--guest`: the same, with 0 cookies in the default context. Edge still created and signed in the `Default` profile alongside the guest one.

## Results

### 1. Does Google allow the sign-in?

**One step, with a DevTools port: no.** The person selected "Continue with Google". The popup went from `about:` to `accounts.google.com/v3/signin/identifier`, then 3 s later to `accounts.google.com/v3/signin/rejected`. Right after the email step, Google showed:

> Couldn't sign you in. This browser or app may not be secure. Learn more. Try using a different browser. If you're already using a supported browser, you can try again to sign in.

The likely reason is `navigator.webdriver`.
- In Edge 156, `--remote-debugging-port` on its own makes `navigator.webdriver` true on every page.
- This was checked without attaching to any tab. A `data:` page put `navigator.webdriver` in its own title, and the title was read from `Target.getTargets`.
- With the port, the page saw `true`. With the same flags minus the port, `false`, read from the window title.
- The user agent doesn't say headless, and nothing else marked the browser. Google doesn't say what it checks, so this is an inference.

Hiding `navigator.webdriver` (for example with `--disable-blink-features=AutomationControlled`) would be working around Google's check, so it wasn't tried.

**Two steps, with no port during the sign-in: yes.** The person signed in with Google, SoundCloud showed them signed in, and they closed the window with its X. Step 1 took 327 s, most of it the person's own time. Step 2 then read both cookies.

The popup itself works in a real Edge. T-003's pywebview window turned it into a blank browser tab and "Please enable popup windows and try again."

### 2. The cookies

Read in step 2 of the two-step run:

| | `oauth_token` | `oauth_refresh_token` |
|---|---|---|
| Domain | `soundcloud.com`, with no leading dot, so it's host-only | `soundcloud.com`, host-only |
| Path | `/` | `/` |
| HttpOnly | no | yes |
| Secure | yes | yes |
| SameSite | not set | not set |
| Cookie expiry | 365 days | 182 days |
| Length | 861, starting `eyJ` | 32 |

The access token inside is still a 24-hour JWT. The 365-day cookie expiry only says how long the browser keeps the cookie. The relaunch found 51 soundcloud.com cookies in all, so the profile keeps cookies across a normal close.

### 3. `/me`

`core.auth.sign_in` with the two values returned 200 from `/me`, and the account's name and plan came back. The plan was free. `sign_in` stored the pair in the in-memory keyring.

### 4. One renewal

`core.auth.renew` with the window's refresh token worked.
- The returned refresh token was **different** from the one sent, so SoundCloud rotates it on every renewal. T-003 couldn't check this.
- The new access token had 24.00 hours left.
- `core.auth.validate` with the new access token got 200 from `/me`.

Not tested, since the run made one renewal only: whether the old refresh token still works after a renewal.

### 5. With the person's own Edge open

The person's Edge wasn't running during either sign-in run (0 other `msedge.exe` processes before and after). So this was checked with a stand-in: a first Edge on its own temporary profile, with a port so its tabs could be listed.
- A second Edge started with the spike's flags, both with and without the port. Both times it kept running as its own browser, and the first Edge still had only its `about:blank` tab.
- With the port, the second Edge wrote its own `DevToolsActivePort`.
- Edge allows one browser per `--user-data-dir`, and a new folder never matches the person's profile. An Edge that handed its URL to a running Edge would exit at once, and `edge.wait_for_port` treats that as an error.

### 6. The DevTools port

- It listens on `127.0.0.1` only, as netstat shows. Connecting through the machine's 3 LAN addresses was refused.
- A WebSocket that sends a web page's `Origin` is refused. That's Chromium's `--remote-allow-origins` check, so a web page can't drive the browser.
- Any local process can still connect. `/json/version` answers with no key, and the browser target can read every cookie.
- How long it was open:
  - One step: the whole sign-in. That was 600 s in the run that timed out, and 90 s in the blocked run.
  - Two steps: 0.2 s in the real run, from the port file appearing to the exit. Step 2 took 0.4 s from start to exit.

What reduces the exposure:
- The two-step design. No port is open while the person signs in, so the port can't see the Google password page.
- In step 2, `about:blank` and one `Storage.getCookies` call before `Browser.close`.
- `--remote-debugging-pipe` would remove the port entirely, but this wasn't tried. On Windows it needs inherited pipe handles, which Python's `subprocess` doesn't pass as file descriptors 3 and 4. Chromium most likely counts it as automation too, which doesn't matter in a headless step that loads no page.

### 7. Closing the window early

- **One step:** yes, it's noticed. The self-test sent `WM_CLOSE` after 5 polls. Edge exited, since a new profile doesn't keep running in the background, and the script noticed after 1.2 s. The script would also notice an Edge that runs on with no page left, but that wasn't seen.
- **Two steps:** step 1 ends when the Edge process exits, so a close is always noticed. The script can't tell a close after signing in from one before, because nothing is attached. Step 2 then reports which cookies are missing.
- **Stopped by process id:** in a self-test, step 1 was stopped by process id after 6 s instead of being closed. The relaunch then found no soundcloud.com cookies at all, so Edge hadn't saved them. A normal close saves them. So step 1 must end with the person's own close or `WM_CLOSE`, never a kill.

### Clean-up

Every run deleted its temporary profile and left no process naming it. Edge was stopped by process id twice: the blocked run, ended on request, and the timeout self-test. Every other run ended with `Browser.close` or the window's own close.
