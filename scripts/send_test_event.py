"""Send a correctly signed (or deliberately bad) test event to a running server.
Usage: python scripts/send_test_event.py [--url URL] [--id evt_1] [--bad]"""
import argparse, json, os, time
import httpx, stripe

p = argparse.ArgumentParser()
p.add_argument("--url", default="http://localhost:8000/webhook")
p.add_argument("--id", default="evt_demo_1")
p.add_argument("--type", default="checkout.session.completed")
p.add_argument("--bad", action="store_true", help="send an invalid signature")
a = p.parse_args()

secret = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
event = {"id": a.id, "type": a.type,
         "data": {"object": {"id": "cs_demo_1", "amount_total": 4900, "currency": "usd"}}}
payload = json.dumps(event)
ts = int(time.time())
sig = "bad" if a.bad else stripe.WebhookSignature._compute_signature(f"{ts}.{payload}", secret)
r = httpx.post(a.url, content=payload,
               headers={"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"})
print(r.status_code, r.text)
