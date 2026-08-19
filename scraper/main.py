"""
scraper/main.py

Thin API wrapper around the pipeline so it's a "deployed working demo"
per the brief, not just a script. In production this target URL would be
an env var pointing at a real permissive source; for the demo it points
at our own mock_target sandbox (see ALLOWED_HOSTS guardrail in scraper.py).
"""

import os
from fastapi import FastAPI
from scraper import Scraper

app = FastAPI(title="Job Ingestion Demo")

TARGET_URL = os.environ.get("TARGET_URL", "http://127.0.0.1:5050")

LAST_RESULTS: list[dict] = []


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/run")
async def run_scrape():
    global LAST_RESULTS
    scraper = Scraper(TARGET_URL)
    LAST_RESULTS = await scraper.run()
    return {"scraped": len(LAST_RESULTS), "jobs": LAST_RESULTS}


@app.get("/jobs")
def get_jobs():
    return {"count": len(LAST_RESULTS), "jobs": LAST_RESULTS}
