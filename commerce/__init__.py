"""
Cognitive Commerce: Swiggy MCP integration (Food/Instamart/Dineout as
external action connectors on top of the existing cognitive/EI layer).

Entirely additive and optional -- see swiggy_adapter.py, the only module in
this package app.py imports from. Gated behind SWIGGY_MCP_ENABLED in .env,
same shape as emotional_intelligence/ei_adapter.py's own flag. Nothing in
here is imported or executed unless that flag is on.

See SWIGGY_MCP_COGNITIVE_COMMERCE_PLAN.md at the repo root for the full
design and the list of things that still need re-verifying against Swiggy's
live docs/dev environment before this goes anywhere near production.
"""
