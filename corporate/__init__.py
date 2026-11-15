"""Throughline Corporate -- organisations, their departments and teams, and
who belongs where. The foundation the organisational-memory features are
built on, one step at a time.

Self-contained, same isolation convention as meet/ and ops/: nothing here
imports app.py; the host app hands in what it needs (DB connection, login
check, current user, email sender) when the blueprint is created. Its tables
live in their own Postgres schema, `corp`, so no existing table is touched.

Off unless CORPORATE_ENABLED=true. The only lines app.py needs:

    from corporate import create_corporate_blueprint, is_enabled
    if is_enabled():
        app.register_blueprint(create_corporate_blueprint(...))

Modules:
  config.py      the flag and tunables
  schema.py      the corp.* tables, created on first use
  roles.py       who may do what -- pure functions, no DB
  orgs.py        organisations and their members
  units.py       departments and teams (a tree), and their members
  invites.py     invitation links
  routes.py      the HTTP API (/corp/...)"""
from .config import is_enabled
from .routes import create_corporate_blueprint

__all__ = ["create_corporate_blueprint", "is_enabled"]
