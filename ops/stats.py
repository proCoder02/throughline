"""EI extraction / weekly digest / FCM / database / system stats for the
admin dashboard -- pure DB and OS read logic, no Flask dependency."""
from __future__ import annotations

import os
import shutil


def ei_realtime_stats(cur) -> dict:
    """Derived entirely from existing emotional_intelligence.analysis_runs
    rows -- no new writes needed, since real_user_extraction.py and
    chat_feedback_extraction.py (both real-time and batch invocations)
    already write one there per conversation/message processed."""
    cur.execute(
        """
        SELECT run_type,
               count(*) FILTER (WHERE status = 'success') AS success,
               count(*) FILTER (WHERE status != 'success') AS error,
               max(created_at) AS last_at
        FROM emotional_intelligence.analysis_runs
        WHERE created_at > now() - interval '24 hours'
          AND run_type IN ('chat_feedback_extraction_realtime', 'chat_feedback_extraction',
                            'real_user_extraction', 'personality_batch', 'relationship_batch', 'fact_dedup')
        GROUP BY run_type
        """
    )
    return {
        r["run_type"]: {
            "success": r["success"], "error": r["error"],
            "last_at": r["last_at"].isoformat() if r["last_at"] else None,
        }
        for r in cur.fetchall()
    }


def weekly_digest_stats(cur) -> dict:
    cur.execute(
        "SELECT count(*) AS n, max(sent_at) AS last_at FROM nudges.sent_log "
        "WHERE nudge_type = 'weekly_digest' AND sent_at > now() - interval '7 days'"
    )
    r = cur.fetchone()
    return {"sent_last_7d": r["n"], "last_sent_at": r["last_at"].isoformat() if r["last_at"] else None}


def fcm_stats(cur) -> dict:
    """Derived from ops.fcm_sends, written by app.py's send_fcm_to_user via
    ops.tracking.record_fcm_send -- 'no_token' means the push never reached
    Firebase at all (no device registered), distinct from 'error' (Firebase
    rejected or the call itself failed) and 'stale' (a token that's since
    been deleted)."""
    cur.execute(
        """
        SELECT status, count(*) AS n
        FROM ops.fcm_sends
        WHERE created_at > now() - interval '24 hours'
        GROUP BY status
        """
    )
    by_status = {r["status"]: r["n"] for r in cur.fetchall()}
    cur.execute("SELECT max(created_at) AS last_at FROM ops.fcm_sends")
    last_at = cur.fetchone()["last_at"]
    cur.execute("SELECT count(*) AS n FROM push_tokens")
    registered_tokens = cur.fetchone()["n"]
    return {
        "last_24h": by_status,
        "last_send_at": last_at.isoformat() if last_at else None,
        "registered_tokens": registered_tokens,
    }


def db_stats(cur) -> dict:
    cur.execute("SELECT pg_database_size(current_database()) AS bytes")
    db_bytes = cur.fetchone()["bytes"]
    cur.execute(
        """
        SELECT schemaname || '.' || relname AS table_name,
               pg_total_relation_size(schemaname || '.' || relname) AS bytes
        FROM pg_catalog.pg_statio_user_tables
        ORDER BY bytes DESC
        LIMIT 8
        """
    )
    largest = [{"table": r["table_name"], "bytes": r["bytes"]} for r in cur.fetchall()]
    return {"total_bytes": db_bytes, "largest_tables": largest}


def system_stats() -> dict:
    """Linux-only (os.getloadavg(), /proc/meminfo) -- this dashboard only
    ever runs against the real prod host, the same single Oracle Cloud VM
    that runs Postgres (DATABASE_URL is localhost), so shutil.disk_usage('/')
    reports the real volume both the app and the DB live on. Falls back to
    an "error" field per section rather than failing the whole endpoint if
    run somewhere these aren't available (e.g. local Windows dev)."""
    out = {}
    try:
        total, used, free = shutil.disk_usage("/")
        out["disk"] = {
            "total_gb": round(total / 1e9, 1), "used_gb": round(used / 1e9, 1),
            "free_gb": round(free / 1e9, 1), "percent_used": round(used / total * 100, 1),
        }
    except Exception as e:
        out["disk"] = {"error": str(e)}
    try:
        load1, load5, load15 = os.getloadavg()
        out["cpu"] = {"load_1m": round(load1, 2), "load_5m": round(load5, 2),
                      "load_15m": round(load15, 2), "core_count": os.cpu_count()}
    except Exception as e:
        out["cpu"] = {"error": str(e)}
    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                meminfo[key] = int(rest.strip().split()[0])  # kB
        total_kb = meminfo.get("MemTotal", 0)
        available_kb = meminfo.get("MemAvailable", 0)
        used_kb = total_kb - available_kb
        out["memory"] = {
            "total_mb": round(total_kb / 1024, 1), "available_mb": round(available_kb / 1024, 1),
            "used_mb": round(used_kb / 1024, 1),
            "percent_used": round(used_kb / total_kb * 100, 1) if total_kb else None,
        }
    except Exception as e:
        out["memory"] = {"error": str(e)}
    return out
