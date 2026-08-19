# Job Listing Ingestion Demo

Two pieces:
- `mock_target/` — a small Flask "job board" that simulates real anti-bot defenses
  (UA filtering, rate limiting, timing checks, flaky 5xxs, markup drift). This is the
  sandbox the scraper is run against, per the assignment's scope guardrail.
- `scraper/` — the ingestion pipeline (`scraper.py`) plus a FastAPI wrapper (`main.py`)
  so it's a deployable demo, not just a script.

See `DESIGN.md` for the design write-up and `DECISIONS.md` for the required 1-pager.

## Run locally

```bash
pip install -r requirements.txt

# terminal 1
cd mock_target && python3 app.py        # serves on :5050

# terminal 2 — run the pipeline directly
cd scraper && python3 scraper.py

# OR run it behind the API
cd scraper && uvicorn main:app --reload --port 8000
# then: curl -X POST http://localhost:8000/run
#       curl http://localhost:8000/jobs
```

## Deploy (Render/Railway)

Deploy **both** services (they're independent processes):

1. **mock_target**: deploy `mock_target/app.py` as its own web service
   (start command: `python app.py`, or `gunicorn app:app` for production).
2. **scraper API**: deploy `scraper/main.py` as a second web service
   (start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`), with
   `TARGET_URL` set to the mock_target service's public URL.
   - Remember to add that public host to `ALLOWED_HOSTS` in `scraper.py` —
     the guardrail is intentionally strict by default.

Then the "deployed working demo" is: `POST /run` on the scraper service pulls
listings from the deployed mock_target and `GET /jobs` shows the results.
