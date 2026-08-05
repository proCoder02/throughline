"""
One-time: applies emotional_intelligence_schema.sql, same pattern as
postgres_setup.py / apply_relationship_schema.py (reads DATABASE_URL from
.env). Safe to re-run.

Usage:
    python apply_emotional_intelligence_schema.py
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

try:
    import psycopg2
except ImportError:
    print("Missing dependency. Run: pip install psycopg2-binary")
    sys.exit(1)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("DATABASE_URL is not set. Add it to your .env file.")
    sys.exit(1)

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "emotional_intelligence_schema.sql")

with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
    schema_sql = f.read()

print(f"Connecting to {DATABASE_URL.split('@')[-1]}...")
conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cur = conn.cursor()

print("Applying emotional_intelligence_schema.sql...")
cur.execute(schema_sql)

cur.execute(
    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'emotional_intelligence' ORDER BY table_name"
)
ei_tables = [row[0] for row in cur.fetchall()]
print(f"Tables now in emotional_intelligence: {', '.join(ei_tables)}")

cur.execute(
    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"
)
public_tables = [row[0] for row in cur.fetchall()]
print(f"Tables remaining in public: {', '.join(public_tables)}")

cur.close()
conn.close()
print("Done.")
