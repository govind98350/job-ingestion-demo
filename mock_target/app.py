"""
mock_target/app.py

A small, self-contained "job board" that simulates the anti-bot defenses
real platforms use. This is OUR sandbox -- we own it, so scraping it raises
no ToS or ethics issues. It exists purely so the scraper has something
realistic to fight against.

Defenses implemented (on purpose, to mirror real sites):
1. User-Agent filtering  -> blocks empty/known-bot UAs with 403
2. Missing browser-like headers -> blocks requests missing Accept-Language
3. Rate limiting per IP   -> 429 after N requests in a rolling window
4. Behavioral / timing detection -> flags requests fired too regularly
   (real bots often hit endpoints at suspiciously constant intervals)
5. Random flakiness       -> ~8% of requests return empty/500, to force
   the scraper to handle partial failure
6. Markup drift           -> the HTML structure for listings flips between
   two different layouts every few minutes, to force the scraper to not
   depend on a single brittle selector
"""

import random
import time
from collections import defaultdict, deque
from flask import Flask, request, Response, jsonify

app = Flask(__name__)

# ---- fake data -------------------------------------------------------
JOBS = [
    {"id": 1, "title": "Backend Engineer", "company": "Northwind", "location": "Remote"},
    {"id": 2, "title": "Frontend Engineer", "company": "Acme Corp", "location": "Bengaluru"},
    {"id": 3, "title": "Data Engineer", "company": "Initech", "location": "Remote"},
    {"id": 4, "title": "DevOps Engineer", "company": "Globex", "location": "Amritsar"},
    {"id": 5, "title": "ML Engineer", "company": "Hooli", "location": "Remote"},
    {"id": 6, "title": "Product Designer", "company": "Umbrella", "location": "Delhi"},
    {"id": 7, "title": "QA Engineer", "company": "Stark Industries", "location": "Remote"},
    {"id": 8, "title": "Site Reliability Engineer", "company": "Wayne Enterprises", "location": "Remote"},
]

BLOCKED_UA_SUBSTRINGS = ["headlesschrome", "python-requests", "curl/", "bot", "scrapy", "puppeteer"]

# ---- naive in-memory tracking (fine for a demo, would be Redis in prod) ----
REQUEST_LOG = defaultdict(lambda: deque(maxlen=20))  # ip -> timestamps
RATE_LIMIT_WINDOW_S = 30
RATE_LIMIT_MAX_REQ = 6


def client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr)


def is_bot_ua(ua: str) -> bool:
    ua = (ua or "").lower()
    if not ua:
        return True
    return any(sig in ua for sig in BLOCKED_UA_SUBSTRINGS)


def looks_too_regular(timestamps) -> bool:
    """Flag suspiciously constant intervals between requests (bot-like)."""
    if len(timestamps) < 4:
        return False
    gaps = [t2 - t1 for t1, t2 in zip(timestamps, list(timestamps)[1:])]
    if not gaps:
        return False
    avg = sum(gaps) / len(gaps)
    variance = sum((g - avg) ** 2 for g in gaps) / len(gaps)
    # very low variance in timing = looks scripted
    return variance < 0.02 and avg < 2.0


@app.before_request
def gate():
    if request.path == "/health":
        return None

    ua = request.headers.get("User-Agent", "")
    accept_lang = request.headers.get("Accept-Language")

    if is_bot_ua(ua):
        return jsonify({"error": "forbidden", "reason": "user-agent"}), 403

    if not accept_lang:
        return jsonify({"error": "forbidden", "reason": "missing headers"}), 403

    ip = client_ip()
    now = time.time()
    log = REQUEST_LOG[ip]
    log.append(now)

    recent = [t for t in log if now - t <= RATE_LIMIT_WINDOW_S]
    if len(recent) > RATE_LIMIT_MAX_REQ:
        return jsonify({"error": "rate_limited", "retry_after_s": RATE_LIMIT_WINDOW_S}), 429

    if looks_too_regular(log):
        return jsonify({"error": "captcha_required", "hint": "vary your timing"}), 429

    # random flakiness to force resilience handling
    if random.random() < 0.08:
        return jsonify({"error": "server_hiccup"}), 500


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/jobs")
def jobs():
    """
    Listings endpoint. Markup layout alternates every ~90s to simulate a
    site redesign / markup drift, so the scraper can't hardcode one shape.
    """
    page = int(request.args.get("page", 1))
    per_page = 3
    start = (page - 1) * per_page
    chunk = JOBS[start:start + per_page]

    layout_b = int(time.time() // 90) % 2 == 1

    if not layout_b:
        rows = "".join(
            f'<div class="job-card" data-id="{j["id"]}">'
            f'<h2 class="job-title">{j["title"]}</h2>'
            f'<span class="job-company">{j["company"]}</span>'
            f'<span class="job-location">{j["location"]}</span>'
            f'</div>'
            for j in chunk
        )
    else:
        # "redesigned" markup -- different tags/classes, same data
        rows = "".join(
            f'<li class="listing" id="listing-{j["id"]}">'
            f'<p class="role-name">{j["title"]}</p>'
            f'<p class="employer">{j["company"]}</p>'
            f'<p class="loc">{j["location"]}</p>'
            f'</li>'
            for j in chunk
        )

    next_link = f'<a href="/jobs?page={page + 1}" class="next">Next</a>' if start + per_page < len(JOBS) else ""

    html = f"""
    <html><body>
      <div id="listings">{rows}</div>
      {next_link}
    </body></html>
    """
    return Response(html, mimetype="text/html")


if __name__ == "__main__": import os port = int(os.environ.get("PORT", 5050)) app.run(host="0.0.0.0", port=port, debug=False)
