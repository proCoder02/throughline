"""Tables for Pay memory, in their own schema (like corp.*) so the shared
transactions/tasks tables are never altered. Created on first use."""
from __future__ import annotations

import threading

DDL = """
CREATE SCHEMA IF NOT EXISTS pay;

-- One row per payment made from the Pay screen: the UPI ID is what lets
-- Throughline remember a payee (transactions only keeps a display name).
CREATE TABLE IF NOT EXISTS pay.payments (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    vpa TEXT NOT NULL,
    payee_name TEXT,
    amount NUMERIC(12,2) NOT NULL,
    note TEXT,
    app TEXT,
    transaction_id INTEGER,
    task_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS payments_user_vpa ON pay.payments (user_id, lower(vpa), created_at DESC);

-- The user's own name for a payee ("Ramesh (milk)").
CREATE TABLE IF NOT EXISTS pay.payees (
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    vpa TEXT NOT NULL,
    label TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, vpa)
);

-- Whether an open task is money the user has to pay, decided once per task
-- (by the AI) and kept, so "You owe" never re-asks about the same task.
CREATE TABLE IF NOT EXISTS pay.task_money (
    task_id INTEGER PRIMARY KEY REFERENCES tasks (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    is_payment BOOLEAN NOT NULL,
    amount NUMERIC(12,2),
    person TEXT,
    reason TEXT,
    dismissed BOOLEAN NOT NULL DEFAULT FALSE,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_lock = threading.Lock()
_ensured = False


def ensure(cur, conn) -> None:
    global _ensured
    if _ensured:
        return
    with _lock:
        if _ensured:
            return
        cur.execute(DDL)
        conn.commit()
        _ensured = True
