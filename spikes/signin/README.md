# Spike: SoundCloud sign-in window

Spike B from the tech spec. It checks whether the app can sign a person in to SoundCloud in a pywebview window, read the `oauth_token` cookie, and use that token for `/me` and for yt-dlp.

Tested on 2026-10-18 on Windows 11 with pywebview 6.2.1, the WebView2 runtime 155, yt-dlp 2026.08.19 and Python 3.13. The account was a free SoundCloud account that signs in with Google. No Go+ account was tested.

## Running it

The script is Windows only, because it reaches into WebView2 through pythonnet.

| Command | What it does |
|---|---|
| `uv run --locked python spikes/signin/signin_spike.py login` | Opens SoundCloud's sign-in page in a private pywebview window and waits up to 10 minutes for the `oauth_token` cookie. Then it runs the `/me` and yt-dlp checks, and renews with the window's `oauth_refresh_token` using the access token's own client_id. `--max-calls` limits the renewal calls. |
| `uv run --locked python spikes/signin/signin_spike.py paste` | Opens a small box titled "sc-mp3 token test" where you paste the `oauth_token` cookie value from your browser. Then it runs the same checks. |
| `uv run --locked python spikes/signin/signin_spike.py paste --lifetime-only` | Same box, but it only decodes the token's JWT header and times on your machine and makes one `/me` call. |
| `uv run --locked python spikes/signin/signin_spike.py refresh` | Two boxes titled "sc-mp3 refresh test": the `oauth_token`, then the `oauth_refresh_token` from the same sign-in. Renews with the client_id named in the access token. `--max-calls` limits the renewal calls. |
| `uv run --locked python spikes/signin/signin_spike.py refresh --compare-client` | The same two boxes. Compares the access token's client_id claim with the web client_id, and renews once with the claim's client_id only when they differ. This is how attempt 3 below was run. |
| `uv run --locked python spikes/signin/signin_spike.py probe --shots <folder>` | Clicks the sign-in buttons on its own, with no account, and saves screenshots in a new subfolder of `<folder>` for each run. Keep the folder outside the repo. |
| `uv run --locked pytest spikes/signin` | Runs the spike's unit tests. |

How the token is handled:

- It stays in memory. The script never writes it to a file.
- Output shows only its length and first 4 characters. The refresh token gets the same treatment.
- Every printed line goes through `redact.py`, which masks every token value it has been given, `2-…` and JWT token shapes (also right after URL encoding such as `%22`), `OAuth` header values and email addresses.
- The refresh token seen is 32 characters, the same shape as a client_id, so no pattern can tell it apart. It's masked by value only, so the script registers it before printing anything.
- The client_id from the access token is used only to form the request. It's never printed, and it's masked by value too.
- `/me` values are replaced by their type name, except the two subscription ids.
- For `/sign-in` responses, the script reads the body only when the sign-in failed. A successful response carries the token.

## Files

| File | Role |
|---|---|
| `signin_spike.py` | Command line |
| `report.py` | Printing, all through the redacting printer |
| `login_window.py` | The sign-in window, the cookie polling and the record of what the page showed |
| `webview2_driver.py` | Script in iframes, real clicks, response bodies and screenshots through WebView2 |
| `probe.py` | The automated button checks |
| `token_box.py` | The paste box |
| `token_info.py` | Reads a JWT's algorithm, claim names and times, and no other claim values |
| `account_checks.py` | `GET /me` through yt-dlp's opener, and yt-dlp's own sign-in |
| `refresh_check.py` | Renews an access token with a refresh token |
| `redact.py` | Masking helpers |

## Results

### 1. Login URL

`https://soundcloud.com/signin` opens the form directly, with HTTP 200 and no redirect.

- The form is an iframe from `https://secure.soundcloud.com/web-auth`. The query keys are `client_id`, `device_id`, `theme`, `ui_evo`, `embedded_in_iframe`, `app_id`, `tracking`, `redirect_uri`, `state`, `code_challenge` and `code_challenge_method`, so it's an authorization-code flow with PKCE.
- The iframe was in the page about 2.1 s after the window opened, in all 3 runs: 2.22 s, 2.08 s and 2.07 s.
- The form's heading is "Create an account or sign in". It shows "Continue with Facebook", "Continue with Google" and "Continue with Apple", then a field for "Your email address or profile URL".
- Each page load also sends `POST api-auth.soundcloud.com/allow`, which returned 202. No captcha or bot page appeared on load.
- Each page load also sends `POST api-auth.soundcloud.com/oauth/authorize`, which returned 400 every time (at 2.49 s and 2.27 s in the two windows the person used). A self-test that read the body got `{"error": "precondition_required"}`. The form still loaded and the email step worked, so it looks like a "no session yet" answer, but that's not explained.

The home page loads the same `web-auth` iframe, hidden. The automated click on the home page's "Sign in" didn't open it in two tries, probably because the click hit the wrong element. That result is inconclusive. Use `/signin`.

The email step works in WebView2. Each `GET api-auth.soundcloud.com/web-auth/identifier` returned 200.

The password step is not confirmed.

- **First window.** The account had no password then, because it signs in with Google. `POST api-auth.soundcloud.com/sign-in` returned 403 twice, then 401 six times. The person also requested three password resets, and each returned 202.
- **Second window.** No password was tried. This window also recorded each failed attempt's visible error text, error body and any captcha, but that recording was never exercised with a password.

So it's still unknown whether a correct password signs in from the window. No bot block was seen, but one isn't ruled out.

### 2. Token cookie

Not observed, because no sign-in in the window succeeded.

What was observed:

- `get_cookies()` works in private mode on WebView2. The two windows the person used made 313 checks, once a second and on every page load, with no errors.
- Most checks took 20 ms or less. One check in the first window took 8.0 s. The script now records when the slowest check happens.
- pywebview asks WebView2 only for the current page's cookies. That's enough on soundcloud.com, because `.soundcloud.com` cookies match it.
- pywebview deleted the private-mode WebView2 data folder when each window closed.

The `oauth_token` value pasted from the person's browser is a JWT: 861 characters, starting with `eyJ`. That doesn't match the `2-…` shape in TECH_SPEC §13 and RESEARCH §1.

The token lasts 24 hours. `paste --lifetime-only` decoded it locally:

| | Value |
|---|---|
| Header | `alg` RS256, `typ` `at+JWT` (an OAuth access token in JWT form), plus a `kid` |
| Claim names | `aud`, `cai`, `client_id`, `exp`, `iat`, `iss`, `jti`, `scope`, `sid`, `sub` |
| `exp - iat`, the full lifetime | 86400 s, 24.00 hours |
| `exp - now`, the time left | 85289 s, 23.69 hours |
| `now - iat`, the age | 1111 s, 0.31 hours |

The token was 18.5 minutes old when it was measured, and `/me` accepted it with 200.

So SoundCloud's web app now issues access tokens that expire after a day. It keeps signing people in through the HttpOnly `oauth_refresh_token` cookie, which its web code sets to expire after 182 days. This is the token-refresh flow RESEARCH calls unverified (`isTokenRefreshEnabled`). It's now on.

A token that someone pastes, or that the window reads, stops working within 24 hours unless the app renews it. Question 6 covers renewal.

### 3. Account info

`GET https://api-v2.soundcloud.com/me` through `YoutubeDL.urlopen` with `Authorization: OAuth <token>` returned 200. It needed no `client_id` and no impersonation.

- With no token it returned 401, and with a made-up token, 401.
- `consumer_subscription.product.id` is `free`, not `consumer-free` as TECH_SPEC §13 and RESEARCH §1 say.
- `creator_subscription.product.id` is also `free`.
- `consumer_subscriptions` is null, and `creator_subscriptions` is a list of `{product: {id}}`.

These are the top-level keys. Values were masked.

```
avatar_url, badges{pro, creator_mid_tier, pro_unlimited, verified}, blocked_tracks_count, city,
comments_count, confirmed, consent_management_jwt{userId, jwt}, consumer_subscription{product{id}},
consumer_subscriptions, country_code, cpp, created_at, creator_subscription{product{id}},
creator_subscriptions[{product{id}}], date_of_birth, default_license, default_tracks_feedable,
description, downloads_disabled, downloads_disabled_reason, first_name, followers_count,
followings_count, full_name, gender, groups_count, hidden_tracks_count, id, kind, last_modified,
last_name, likes_count, locale, marketing_ids{gtm}, permalink, permalink_url, playlist_count,
playlist_likes_count, ppid, primary_email, primary_email_confirmed, primary_email_sha256,
private_playlists_count, private_tracks_count, quota{...}, reposts_count, spotlight_limit,
station_permalink, station_urn, track_count, uri, urn, username, verified, visuals, analytics_id
```

`/me` holds the email address, real name, birth date and gender, so it must never be logged.

### 4. Other logins

"Continue with Google" and "Continue with Apple" do nothing useful in the window.

- The click opens a popup. pywebview cancels it and passes the popup's first address, `about:blank`, to `webbrowser.open`.
- SoundCloud's form then shows "Please enable popup windows and try again."
- No Google or Apple page loads in the window or in the browser. SoundCloud opens the popup blank and then points it at the provider by script, and that second step never reaches the browser.
- With pywebview's default handling, the person's browser opens a blank tab. The first window recorded three of these.

This was seen for Google in two windows the person used and in the automated check, and for Apple in two automated runs. Facebook wasn't tested. After the Apple click, the probe's window stopped navigating (no requests at all for the next steps), and the probe had already cleared its list of iframes, so there was no form to search. The button itself was on screen in the screenshot.

The account tested signs in with Google, so the window can't sign it in. Pasting a token is the only path it has.

Nothing was done to get around this. RESEARCH §4 notes that Google blocks OAuth in embedded webviews anyway.

### 5. yt-dlp sign-in

yt-dlp signs in with `username="oauth"` and `password=<token>`. It prints "[soundcloud] Logging in", gives no "Continuing as guest" warning, and holds an `Authorization` header afterwards.

A made-up token gets "Provided authorization token is invalid. Continuing as guest", after the verify request returns 400.

Formats for the same tracks:

| Track | Signed out | Signed in |
|---|---|---|
| `the80m/the-following` | `hls_aac_96k`, `hls_aac_160k`, plus the warning "Original download format is only available for registered users" | `hls_aac_96k`, `hls_aac_160k`, `download` |
| `wandw/the-chainsmokers-ft-daya-dont-let-me-down-ww-remix-1` | `hls_aac_96k`, `hls_aac_160k` | `hls_aac_96k`, `hls_aac_160k` |

With a free account, the subscriber-only AAC HQ format doesn't appear.

One gotcha: yt-dlp keeps the `Authorization` header in a dict on the `SoundcloudBaseIE` class, so every `YoutubeDL` in the process shares it. A later `YoutubeDL` with another token, even an invalid one, skips verification and keeps the old header. Signing out or changing the token has to clear that dict.

### 6. Token refresh

Because the access token lasts 24 hours (question 2), the app has to renew it. The first part below comes from reading SoundCloud's public web code; the second from three live renewal calls. One renewal succeeded, with the client_id named inside the access token.

**How the web app renews it (from its code).**

Source: the web bundle saved on 2026-10-18 from `https://a-v2.sndcdn.com/assets/`, mainly `55-d39036ca.js` (also `56-990ddcf7.js` and `0-5357b47f.js`). Searching a newer bundle for the names below should find the same code again.

- **The request.** A function named `refreshToken` sends `POST` to the token endpoint, form-encoded (`Content-Type: application/x-www-form-urlencoded; charset=UTF-8`), with `client_id`, `grant_type=refresh_token` and `refresh_token`. It adds `client_secret` only when one is configured. The response's `access_token` and `refresh_token` are stored. The endpoint used for sign-in's code exchange is `https://secure.soundcloud.com/oauth/token`, and the live calls below used it for refresh too.
- **Who sends it.** With the `isTokenRefreshEnabled` feature on, the browser doesn't. Its `TokenExpiryManager` (built by `tokenExpiryManagerClientFactory`) POSTs with no body and `credentials: include` to a same-origin proxy, the endpoint named `tokenRefreshProxyWebi` (`api/refresh-token` on `https://soundcloud.com`, under the base path `n`). There's also `tokenRefreshProxy` (`/-/refresh-token`). On SoundCloud's server, `refreshAuthTokenCookie` (from `tokenExpiryManagerServerFactory`) reads the HttpOnly `oauth_refresh_token` cookie, calls `refreshToken`, and sets new `oauth_token` and `oauth_refresh_token` cookies. The refresh cookie is set HttpOnly and secure, with an expiry 182 days out.
- **client_id.** `refreshAuthTokenCookie` decodes the current access token and uses its `client_id` claim when there is one, otherwise the server's configured client_id. The live test below found that claim names a different client from the web client_id yt-dlp scrapes. The sign-in iframe's own client_id wasn't compared.
- **When it renews.** Only after a request comes back `401`. `TokenExpiryManager` then renews once, under a lock so parallel requests wait instead of each renewing, and retries the request. It doesn't check `exp` on a timer.
- **When renewal fails.** `refreshAuthTokenCookie` clears both cookies when the refresh token is missing or the token endpoint answers `401`. The live `invalid_grant` below came back as `400`, which that rule doesn't cover.

**Renewing with a refresh token (three live attempts).**

Each time, the person signed in with Google in a private browser window, pasted cookie values into the box or boxes, and closed the window without signing out.

- **Attempt 1.** The pasted value was 861 characters starting `eyJ`, the same as the access token, so it was most likely the `oauth_token` cookie by mistake. `POST oauth/token` returned 400. The `error` code wasn't printed then.
- **Attempt 2.** With the box now refusing an access token, the person pasted the real `oauth_refresh_token` (32 characters, not a JWT). Renewing with the client_id yt-dlp scrapes returned **400 `invalid_grant`**. A local check confirmed that id is the one `client_id` the web bundle configures, so the client_id wasn't wrong in the obvious way.
- **Attempt 3 (`refresh --compare-client`).** The person pasted the `oauth_token` into box 1 and the `oauth_refresh_token` into box 2, from one sign-in. The access token's `client_id` claim was **different** from the web client_id. Renewing with the claim's own client_id returned **200**.

How attempt 3's request was sent: through yt-dlp's opener (`YoutubeDL.urlopen`), as `POST https://secure.soundcloud.com/oauth/token` with the form body above and `Content-Type: application/x-www-form-urlencoded; charset=UTF-8`. The other headers were yt-dlp's standard ones: its random Chrome `User-Agent`, `Accept`, `Accept-Language` and `Sec-Fetch-Mode: navigate`. There was no `client_secret`, no cookie and no `Origin` header.

What the successful renewal recorded:
- Status 200, with the fields `access_token`, `refresh_token`, `expires_in`, `scope` and `token_type` (`Bearer`).
- `expires_in` was 86399, and the returned access token is the same RS256 `at+JWT`, with a 24-hour `exp - iat`.
- The response included a `refresh_token` field. Whether its value differed from the one sent wasn't recorded. The script then only noted that the field was present; it now also records whether the value changed.

The client_id conclusion is an inference, not a controlled test. Attempts 2 and 3 used different refresh tokens from different sign-ins, and no one refresh token was tried with both client_ids. Two things support it:
- RFC 6749 §5.2 defines `invalid_grant` to include a refresh token "issued to another client".
- SoundCloud's own server renews with the access token's `client_id` claim, not the web client_id.

So the likely rule is: renewal works with the client_id named inside the access token, and not with the web client_id yt-dlp scrapes.

Not tested live, because the three-call budget was spent:
- that the returned refresh token differs from the one sent
- that the old refresh token stops working after a renewal
- that the returned refresh token works for the next renewal
- the new access token on `/me` or with yt-dlp
- the refresh token's own lifetime. The web code's 182-day cookie expiry says how long the browser keeps it, not how long SoundCloud accepts it.
- one refresh token tried with both client_ids

The web code stores whatever `refresh_token` comes back, which is safe either way. It doesn't show whether SoundCloud revokes the old one.

**What this means for the app.** The app can most likely keep a person signed in past 24 hours without a new sign-in. It reads `oauth_token` and `oauth_refresh_token` from the window with `get_cookies()`, reads the `client_id` claim out of the access token, and POSTs to `oauth/token` with that client_id, `grant_type=refresh_token` and the refresh token. It stores whatever `refresh_token` comes back, and never assumes the old one still works. The web client_id yt-dlp scrapes isn't the one to use.

**The `refresh` command.**

`refresh` takes the access token in box 1 and the refresh token in box 2, from the same sign-in. It renews with the client_id named in the access token, the way the app would. It records, with no token values:

- the status and the response field names, and the OAuth `error` code on a failure
- the lifetime of the new access token, from its JWT
- whether a `refresh_token` field came back, and whether its value differs from the one sent
- in order: renew; renew again with the refresh token that came back; then reuse the original one last. The original goes last because servers that detect reuse revoke the whole token family, which would make the returned token look broken if it were tried after.

Each box refuses the wrong kind of token and lets the person paste once more, with no call made. `--max-calls` limits the calls. The reuse step runs only if the returned refresh token differs from the original. `--compare-client` is how attempt 3 was run: it compares the token's `client_id` claim with the web client_id locally, printing only "same" or "different", and renews once with the claim's client_id only when they differ. The fake-server unit tests cover rotating, non-rotating and reuse-detecting servers.

**The refresh cookie in the window (not yet observed).**

No email and password sign-in in the window has succeeded, so `oauth_refresh_token` wasn't read from the window. When a sign-in does succeed, `login` reads it from `get_cookies()`, records its domain, HttpOnly flag, expiry and length, and runs the same renewal test with the access token's client_id. pywebview reads HttpOnly cookies on WebView2, so the cookie should be readable there even though page JavaScript can't see it.
