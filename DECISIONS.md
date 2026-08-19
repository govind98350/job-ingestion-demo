# DECISIONS.md

## 1. Why this ingestion strategy over the obvious alternative I rejected?

The obvious alternative was a single `requests.get()` loop with a fixed `time.sleep(2)`
between calls and one hardcoded User-Agent. I rejected it because it fails against exactly
the defenses real job boards use: a fixed sleep interval is itself a bot signal (real
users don't click at perfectly even intervals), and a single UA/session means one 403
ends the whole run. The identity-pool + jittered-pacing design costs more code up front
but survives the failure modes the brief explicitly asks about — a source blocking you
mid-run, not just a source that never blocks you.

I also considered going straight to a headless browser (Playwright) for everything, since
it defeats more fingerprinting checks by default. I rejected that as the *default* path
because it's heavier (slower, harder to scale, easier to still get flagged via WebDriver
tells) and most listing pages don't need JS rendering. Instead the fetch layer is isolated
behind one method (`fetch_page`) specifically so it can drop in a Playwright-backed
implementation later without touching retry/parsing/pacing logic — pay that cost only
when a source actually requires it.

## 2. One trade-off I made under the time limit

The mock target's markup drift is time-based (flips every ~90s) rather than triggered by
an actual "redesign" event, and identity rotation is in-memory with no persistence across
restarts. Both are fine for a demo but not production-real. With a real week I'd:
- persist identity state (which are burned, cool-down timers) to Redis so a redeploy
  doesn't reset the pool,
- add a small test suite that snapshots both markup layouts so a real drift is caught
  by CI, not discovered in production,
- replace the "4 static retries" with a circuit breaker per source so a fully-down
  target stops getting hit at all instead of retrying forever.

## 3. Where I used AI tools, and what I verified/changed afterward

I used Claude to scaffold the initial project structure and first draft of the mock
target's anti-bot logic (UA blocklist, rate-limit window, the two alternating HTML
layouts) and the scraper's retry/backoff and identity-rotation classes. I then:
- ran the mock server and scraper together locally and read the logs line-by-line to
  confirm the rotation/backoff actually fires on 403/429 rather than just looking
  plausible in the code,
- deliberately curl'd the mock target with a bot UA, no `Accept-Language`, and a
  rapid-fire loop to verify each defense triggers the response I expected,
- rewrote the `ALLOWED_HOSTS` guardrail myself after the first draft only mentioned
  the ethical line in the design doc but didn't enforce it in code — I wanted a line
  I couldn't accidentally cross under deadline pressure, not just a stated intention,
- can explain and would defend every function in `scraper.py` line-by-line in the
  follow-up call, including why jitter uses a Gaussian rather than uniform distribution
  (uniform still has a detectable ceiling/floor; Gaussian tails look more human).
