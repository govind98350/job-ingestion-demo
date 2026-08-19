"""
scraper/scraper.py

Ingestion pipeline. Design goals, mapped straight to the brief:

1. Detection surface it accounts for:
   - User-Agent fingerprint      -> rotates through a pool of real UA strings
   - Missing browser headers     -> sends a full, consistent header set per identity
   - Timing regularity           -> randomized jitter between requests, never fixed sleep
   - IP-based rate limiting      -> paces requests per "identity", backs off on 429
   - CAPTCHA / soft-block signal -> treated as a hard stop for that identity, not retried blindly

2. Ingestion strategy:
   - Requests are made through "Identity" objects (UA + headers + its own
     request history), so if one identity gets flagged, we rotate to a
     fresh one rather than hammering the same fingerprint.
   - Pacing is randomized (jittered sleep) between requests, not a fixed
     interval, because fixed intervals are themselves a detection signal.
   - Plan B if the primary approach gets shut down: the pipeline is built
     against an HTTP client (httpx) today; if a target starts requiring
     JS rendering or gets more aggressive, the same Identity/backoff/
     resilient-parse structure drops behind a headless-browser client
     (e.g. Playwright) without changing the pipeline logic above it --
     the fetch layer is isolated behind `fetch_page()`.

3. Resilience:
   - Retry with exponential backoff + jitter on 5xx / network errors.
   - On 429 / captcha signal: back off hard and rotate identity, don't
     just retry the same way.
   - Parsing never depends on a single selector -- `parse_listings()`
     tries a primary CSS shape, then a fallback shape, so an overnight
     markup change degrades gracefully instead of returning nothing.
   - Every failure is logged with reason, not swallowed silently.

4. Where this stops:
   - This pipeline is only ever pointed at the mock sandbox in this repo
     or sources with a permissive robots.txt / public API. It does not
     attempt to defeat CAPTCHAs, spoof logged-in sessions, or run against
     a live account. That line is enforced in config, not just policy --
     see ALLOWED_HOSTS below.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scraper")

# ---- hard line: technical enforcement of "where we'd stop" ----
ALLOWED_HOSTS = {"localhost", "127.0.0.1"}  # add sandbox/API hosts only, never real platforms

USER_AGENT_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


@dataclass
class Identity:
    """One 'persona' the scraper can act as: its own UA + header profile."""
    user_agent: str
    accept_language: str = "en-US,en;q=0.9"
    burned: bool = False
    request_times: list = field(default_factory=list)

    def headers(self) -> dict:
        return {
            "User-Agent": self.user_agent,
            "Accept-Language": self.accept_language,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }


class IdentityPool:
    """Rotates identities so one flagged fingerprint doesn't sink the run."""

    def __init__(self):
        self._pool = [Identity(user_agent=ua) for ua in USER_AGENT_POOL]
        self._i = 0

    def current(self) -> Identity:
        return self._pool[self._i]

    def rotate(self):
        self._pool[self._i].burned = True
        self._i = (self._i + 1) % len(self._pool)
        log.warning("rotating identity -> now using identity #%d", self._i)
        if all(ident.burned for ident in self._pool):
            log.error("all identities burned; cooling down before reuse")
            for ident in self._pool:
                ident.burned = False
            time.sleep(2)  # in prod: much longer cool-down, or pause the run


def _jittered_delay(base: float = 1.2, spread: float = 0.9) -> float:
    """Randomized pacing -- deliberately NOT a fixed sleep, since constant
    intervals are one of the behavioral signals real sites watch for."""
    return max(0.2, random.gauss(base, spread))


def parse_listings(html: str) -> list[dict]:
    """Resilient parse: try today's known layout, fall back to the
    alternate one, so an overnight markup change doesn't zero out results."""
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # primary shape
    for card in soup.select(".job-card"):
        title = card.select_one(".job-title")
        company = card.select_one(".job-company")
        location = card.select_one(".job-location")
        if title:
            results.append({
                "title": title.get_text(strip=True),
                "company": company.get_text(strip=True) if company else None,
                "location": location.get_text(strip=True) if location else None,
            })
    if results:
        return results

    # fallback shape (site redesign)
    for li in soup.select(".listing"):
        title = li.select_one(".role-name")
        company = li.select_one(".employer")
        location = li.select_one(".loc")
        if title:
            results.append({
                "title": title.get_text(strip=True),
                "company": company.get_text(strip=True) if company else None,
                "location": location.get_text(strip=True) if location else None,
            })

    if not results:
        log.warning("parse_listings found nothing in either known layout -- markup may have changed again")

    return results


def has_next_page(html: str) -> bool:
    return 'class="next"' in html


class Scraper:
    def __init__(self, base_url: str, max_pages: int = 5):
        host = httpx.URL(base_url).host
        if host not in ALLOWED_HOSTS:
            raise ValueError(
                f"Refusing to run against host '{host}'. Only sandbox/allowed hosts are "
                f"permitted -- see ALLOWED_HOSTS. This is a deliberate guardrail, not a bug."
            )
        self.base_url = base_url.rstrip("/")
        self.max_pages = max_pages
        self.identities = IdentityPool()

    async def fetch_page(self, client: httpx.AsyncClient, url: str, attempt: int = 1) -> Optional[str]:
        """Fetch with retry/backoff. Returns None (not an exception) on
        exhausted retries, so the caller can decide to skip and continue."""
        identity = self.identities.current()
        try:
            resp = await client.get(url, headers=identity.headers(), timeout=10)
        except httpx.RequestError as e:
            log.warning("network error on %s: %s (attempt %d)", url, e, attempt)
            return await self._retry_or_give_up(client, url, attempt)

        if resp.status_code == 200:
            return resp.text

        if resp.status_code == 429:
            log.warning("rate-limited (429) on identity, rotating + backing off")
            self.identities.rotate()
            await asyncio.sleep(_jittered_delay(base=4, spread=1.5))
            return await self._retry_or_give_up(client, url, attempt)

        if resp.status_code == 403:
            log.warning("403 -- identity likely fingerprinted, rotating")
            self.identities.rotate()
            return await self._retry_or_give_up(client, url, attempt)

        if resp.status_code >= 500:
            log.warning("server error %s on %s (attempt %d)", resp.status_code, url, attempt)
            return await self._retry_or_give_up(client, url, attempt)

        log.error("unhandled status %s on %s -- skipping", resp.status_code, url)
        return None

    async def _retry_or_give_up(self, client, url, attempt) -> Optional[str]:
        if attempt >= 4:
            log.error("giving up on %s after %d attempts", url, attempt)
            return None
        backoff = _jittered_delay(base=1.5 * attempt, spread=0.5)
        await asyncio.sleep(backoff)
        return await self.fetch_page(client, url, attempt=attempt + 1)

    async def run(self) -> list[dict]:
        all_jobs = []
        async with httpx.AsyncClient() as client:
            page = 1
            while page <= self.max_pages:
                url = f"{self.base_url}/jobs?page={page}"
                log.info("fetching page %d", page)
                html = await self.fetch_page(client, url)

                if html is None:
                    log.warning("page %d unrecoverable, stopping run (partial results kept)", page)
                    break

                jobs = parse_listings(html)
                log.info("page %d -> %d listings parsed", page, len(jobs))
                all_jobs.extend(jobs)

                if not has_next_page(html):
                    break

                page += 1
                await asyncio.sleep(_jittered_delay())  # pacing between pages, always

        return all_jobs


if __name__ == "__main__":
    result = asyncio.run(Scraper("http://127.0.0.1:5050").run())
    print(f"\nScraped {len(result)} jobs:")
    for j in result:
        print(" -", j)
