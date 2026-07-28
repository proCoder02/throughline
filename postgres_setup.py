"""
One-time setup: creates all tables/indexes in your Postgres database.

Usage:
    pip install psycopg2-binary python-dotenv
    python setup_postgres.py

Reads DATABASE_URL from your .env file, e.g.:
    DATABASE_URL=postgresql://thoruline:0326@localhost:5432/speech2text

If the database itself doesn't exist yet, create it first:
    createdb -U thoruline speech2text
    (Windows: run this from the "SQL Shell (psql)" that came with your
    Postgres install, or from a regular terminal if psql is on your PATH)
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
    print("DATABASE_URL is not set. Add it to your .env file, e.g.:")
    print("  DATABASE_URL=postgresql://thoruline:0326@localhost:5432/speech2text")
    sys.exit(1)

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")

with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
    schema_sql = f.read()

print(f"Connecting to {DATABASE_URL.split('@')[-1]}...")  # don't print the password
conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cur = conn.cursor()

print("Applying schema.sql...")
cur.execute(schema_sql)

cur.execute(
    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"
)
tables = [row[0] for row in cur.fetchall()]
print(f"Tables now in database: {', '.join(tables)}")

cur.close()
conn.close()
print("Done. Postgres is ready.")