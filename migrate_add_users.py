"""One-time migration: adds user_id columns to a conversations.db created
before authentication was added, and assigns all existing rows to a
placeholder user so old data isn't lost.

Usage: python migrate_add_users.py
Run this once, from the same folder as conversations.db.
"""
import sqlite3
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash

DB_PATH = "conversations.db"
PLACEHOLDER_USERNAME = "legacy_data"
PLACEHOLDER_PASSWORD = "change-me-please"  # change this after migrating


def column_exists(conn, table, column):
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 1. Make sure a users table exists
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()

    # 2. Create (or find) a placeholder user to own pre-existing rows
    row = conn.execute(
        "SELECT id FROM users WHERE username = ?", (PLACEHOLDER_USERNAME,)
    ).fetchone()
    if row:
        placeholder_id = row["id"]
        print(f"Using existing placeholder user id={placeholder_id}")
    else:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (
                PLACEHOLDER_USERNAME,
                generate_password_hash(PLACEHOLDER_PASSWORD),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        placeholder_id = cur.lastrowid
        conn.commit()
        print(f"Created placeholder user '{PLACEHOLDER_USERNAME}' (id={placeholder_id})")
        print(f"Log in with username='{PLACEHOLDER_USERNAME}' password='{PLACEHOLDER_PASSWORD}' "
              "to see your old data, then change that password.")

    # 3. Add user_id to each table if missing, backfilling with the placeholder id
    for table in ("conversations", "tasks", "personality_notes"):
        if not column_exists(conn, table, "user_id"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER")
            conn.execute(
                f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (placeholder_id,)
            )
            conn.commit()
            print(f"Added user_id to '{table}' and backfilled existing rows.")
        else:
            print(f"'{table}' already has user_id, skipping.")

    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    main()
