# Design Document — Job Listing Ingestion

## 0. What's actually running

The live demo scrapes `mock_target/app.py`, a small Flask app I wrote that
reproduces the anti-bot behavior of real job boards: UA/header filtering,
per-IP rate limiting, behavioral timing checks, random 5xx flakiness, and
markup that alternates layout every ~90s. I built this instead of pointing
at a real board because every real candidate (LinkedIn, Indeed, Naukri,
Wellfound) either requires a login or has ToS explicitly against scraping —
see §4. The mock reproduces the *mechanics* honestly; nothing about the
ingestion pipeline changes if you point `TARGET_URL` at a real permissive
source (a public API or an RSS feed) instead.

## 1. Detection surface

What gives an automated client away, and how this design accounts for it:

| Signal | How real sites use it | How this design handles it |
|---|---|---|
| User-Agent string | Missing, or a known bot/library UA (`python-requests`, `curl`, headless Chrome markers) | `IdentityPool` rotates through real browser UA strings; never sends a default httpx UA |
| Missing browser headers | Real browsers always send `Accept-Language`, `Accept`, etc. | Every `Identity` sends a full, consistent header set, not just a UA override |
| Request timing regularity | Bots often poll at suspiciously constant intervals | Pacing uses `random.gauss` jitter (`_jittered_delay`), never a fixed `sleep(n)` |
| IP-based rate limiting | Too many requests/IP in a window → 429 or ban | Backoff + identity rotation on 429; pipeline paces itself under the observed limit rather than waiting to get blocked |
| Fingerprinting via TLS/JS challenges (headless browser tells) | Canvas fingerprint, WebDriver flags, missing plugins list | **Not defeated in this design** — see §4. If a target requires this, the fetch layer swaps to a real browser context (Playwright with `stealth` patches), which is a fetch-layer change, not a pipeline rewrite |

## 2. Ingestion strategy

- **Rotation**: requests go out through `Identity` objects, not raw calls. Each identity carries its own UA + headers. When one gets a 403/429, it's marked burned and the pool rotates to the next — one flagged fingerprint doesn't take down the whole run.
- **Pacing**: jittered delay between every request (`_jittered_delay`), including between paginated calls. Base delay and spread are tunable; in production this would be calibrated per-source to stay under their observed rate limit rather than a guessed constant.
- **Session/identity management**: identities are cheap and disposable in this demo (UA + headers only). Against a source that requires login, the same pattern extends to holding one authenticated session per identity, with its own cookie jar, and rotating *those* — never reusing a burned session.
- **Plan B when the primary approach gets shut down mid-week**: the fetch layer is isolated behind `Scraper.fetch_page()`. If plain HTTP requests start getting blocked (JS challenges, TLS fingerprinting), that one method swaps to a headless-browser-backed fetch (Playwright) without touching retry logic, parsing, or pacing above it. If the *source itself* cuts off access entirely, the fallback is to widen source diversity (pull the same role data from multiple boards) rather than escalate evasion against one target — see §4 for why.

## 3. Resilience

- **Retry/backoff**: `fetch_page` retries up to 4 times with exponential-ish jittered backoff on network errors and 5xx responses, so a transient hiccup (the mock's random 500s simulate this) doesn't kill the run.
- **429 / soft-block handling**: treated differently from a plain error — backs off harder *and* rotates identity, since retrying identically into a rate limit just extends the block.
- **Markup changes**: `parse_listings()` tries a primary CSS shape first, then a documented fallback shape. The mock target actually flips its HTML structure every ~90 seconds to prove this isn't decorative — a run spanning that window keeps returning full results across the layout change.
- **Silent failure prevention**: every branch (network error, 403, 429, 5xx, empty parse) logs with a distinct reason. A page that can't be recovered after retries is skipped, not fatal — the run keeps partial results instead of returning nothing.

## 4. Where this stops

Every real target listed in the brief (LinkedIn, Indeed, Naukri, Wellfound) prohibits scraping in
its ToS. My line:

- **Technical enforcement, not just intent**: `Scraper.__init__` refuses to run against any host not in `ALLOWED_HOSTS`. That's a hard guardrail in code, not a policy I could accidentally ignore under deadline pressure.
- I will not build or use CAPTCHA-solving, credential stuffing, or logged-in-session automation against a real platform's ToS.
- If a project genuinely needed data from one of these platforms, the correct next step is checking for an official partner/data API or a licensed data provider — not deeper evasion.
- This demo's target is a sandbox I wrote and own, or (if swapped) a source with a public API/permissive robots.txt. That's the boundary the code enforces, not just the boundary I'm claiming in this document.
