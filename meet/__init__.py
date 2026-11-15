"""Throughline Meet -- two people hold their phones together and each sees
the other's personality card. Self-contained, same isolation convention as
ops/: nothing here imports app.py; the few things it needs from the host app
(DB connection, login check, current user, Redis) are handed in when the
blueprint is created. The only line app.py needs is:

    from meet import create_meet_blueprint, is_enabled
    if is_enabled():
        app.register_blueprint(create_meet_blueprint(...))

Off unless MEET_ENABLED=true, so with the flag unset no route, table or
query from this package exists in the running app.

Flow (Bluetooth phase):
  1. POST /meet/session      -> a random code the phone broadcasts over
                                Bluetooth (as part of a service UUID).
  2. POST /meet/encounter    -> "my code is X, I'm touching code Y".
                                Matched once Y reports X within 10 s.
  3. POST /meet/encounter/<id>/confirm -> both must accept.
  4. GET  /meet/encounter/<id>         -> the other person's card, once
                                          both accepted.
See routes.py for the full list."""
from .config import is_enabled
from .routes import create_meet_blueprint

__all__ = ["create_meet_blueprint", "is_enabled"]
