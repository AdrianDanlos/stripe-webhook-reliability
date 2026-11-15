# Stripe Webhook Receiver (reliability demo)

A small FastAPI service that receives Stripe webhooks and does not lose or double-process events.

## What it does
- Verifies the Stripe signature and rejects anything unsigned or tampered with
- Stores every event first, then processes it (nothing is lost if your CRM or database is down)
- Ignores duplicate deliveries (idempotent by Stripe event id)
- Retries failures with exponential backoff, then moves them to a dead-letter state you can inspect
- `GET /health` shows event counts by status (done, retry, dead)

## Run
    pip install -r requirements.txt
    STRIPE_WEBHOOK_SECRET=whsec_xxx uvicorn app:app --port 8000
    stripe listen --forward-to localhost:8000/webhook   # Stripe CLI, test mode
    # call POST /retry-due every minute from cron

## Test
    pytest -q     # 4 tests: bad signature, dedupe, retry then dead-letter, retry recovery

## Adapt
Replace the functions in the `HANDLERS` dict in `app.py` with the real sync logic (CRM, accounting, order system).
