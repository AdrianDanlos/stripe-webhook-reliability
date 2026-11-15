"""Stripe webhook receiver with signature verification, idempotency,
durable event log, retry with backoff, and a dead-letter table.

Flow: verify signature -> store event (dedupe by event id) -> process ->
on failure, schedule retry with exponential backoff -> after MAX_ATTEMPTS, dead-letter.
"""
import json, logging, os, sqlite3, time
import stripe
from fastapi import FastAPI, HTTPException, Request

log = logging.getLogger("webhooks")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_PATH = os.getenv("DB_PATH", "events.db")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "5"))
BASE_DELAY = float(os.getenv("BASE_DELAY_SECONDS", "30"))

app = FastAPI(title="Stripe webhook demo")


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS events(
        id TEXT PRIMARY KEY, type TEXT, payload TEXT,
        status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0,
        next_attempt_at REAL DEFAULT 0, last_error TEXT, received_at REAL);
    CREATE TABLE IF NOT EXISTS orders(
        id TEXT PRIMARY KEY, status TEXT, amount INTEGER, currency TEXT, updated_at REAL);
    """)
    return con


# ---- business handlers: replace these with the client's real sync logic ----
def handle_checkout_completed(con, obj):
    con.execute("INSERT INTO orders(id,status,amount,currency,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at",
                (obj["id"], "paid", obj.get("amount_total"), obj.get("currency"), time.time()))

def handle_payment_failed(con, obj):
    con.execute("INSERT INTO orders(id,status,amount,currency,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at",
                (obj["id"], "failed", obj.get("amount"), obj.get("currency"), time.time()))

HANDLERS = {
    "checkout.session.completed": handle_checkout_completed,
    "payment_intent.payment_failed": handle_payment_failed,
}


def process(con, row):
    """Run one stored event. Returns True on success."""
    event = json.loads(row["payload"])
    handler = HANDLERS.get(row["type"])
    try:
        if handler:
            handler(con, event["data"]["object"])
        con.execute("UPDATE events SET status='done', last_error=NULL WHERE id=?", (row["id"],))
        con.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        attempts = row["attempts"] + 1
        if attempts >= MAX_ATTEMPTS:
            status, nxt = "dead", 0
            log.error("event %s dead-lettered after %d attempts: %s", row["id"], attempts, exc)
        else:
            status, nxt = "retry", time.time() + BASE_DELAY * (2 ** (attempts - 1))
            log.warning("event %s failed (attempt %d): %s", row["id"], attempts, exc)
        con.execute("UPDATE events SET status=?, attempts=?, next_attempt_at=?, last_error=? WHERE id=?",
                    (status, attempts, nxt, str(exc), row["id"]))
        con.commit()
        return False


@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="invalid signature")

    con = db()
    cur = con.execute("INSERT OR IGNORE INTO events(id,type,payload,received_at) VALUES(?,?,?,?)",
                      (event["id"], event["type"], payload.decode(), time.time()))
    con.commit()
    if cur.rowcount == 0:  # duplicate delivery: acknowledge, do not reprocess
        log.info("duplicate event %s ignored", event["id"])
        return {"status": "duplicate"}

    row = con.execute("SELECT * FROM events WHERE id=?", (event["id"],)).fetchone()
    process(con, row)  # always return 200 once stored, retries run from the queue
    return {"status": "received"}


@app.post("/retry-due")
def retry_due():
    """Call from cron every minute (or a background worker) to retry failed events."""
    con = db()
    rows = con.execute("SELECT * FROM events WHERE status='retry' AND next_attempt_at<=?",
                       (time.time(),)).fetchall()
    ok = sum(process(con, r) for r in rows)
    return {"attempted": len(rows), "succeeded": ok}


@app.get("/health")
def health():
    con = db()
    counts = {r["status"]: r["n"] for r in
              con.execute("SELECT status, COUNT(*) n FROM events GROUP BY status")}
    return {"ok": True, "events": counts}
