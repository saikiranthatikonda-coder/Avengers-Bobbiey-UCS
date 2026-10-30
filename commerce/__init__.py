"""PROJECT TRILLION — Agent Commerce & Microtransaction Revenue Engine.

A private, modular extension of BUCS:
  catalog.py    services (id, schemas, price, limits, availability)
  ledger.py     append-only SQLite books with a tamper-evident hash chain;
                separate test and live databases, never mixed
  gateways.py   payment gateway abstraction + Razorpay (orders, server-side
                verification, webhooks, refunds, settlements)
  executors.py  the services that actually run (only reliable ones are sold)
  api.py        public customer/agent API + owner-only admin API

Principles: a service is delivered only after the backend verifies the payment
with the gateway itself; nothing is simulated; every money figure comes from
the ledger, which only records what the gateway confirmed.
"""
