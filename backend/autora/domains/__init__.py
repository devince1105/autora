"""Business domains. Domains never import each other.

A domain is wired in two places, and it is worth knowing which is which:

- ``register(runtime)``, for everything that attaches to a built runtime: services, approval
  callbacks, task hooks, stage hooks. The newsroom has one; a domain with nothing to attach
  does not need one.
- **The composition root (``autora.app``), by name**, for everything that has to exist before a
  runtime does: SQLAlchemy metadata, the event catalog, policy rules, workflow templates, tools,
  KPI hooks, schedules, settings. There is no scan and no plugin discovery — ``app.py`` names
  each domain, and that is the only layer allowed to know they exist.

So deleting a domain's package is not a one-line operation: the composition root, the API
routers and the generated event union all name it (measured 2026-09-22 — see D-021). What the
layering does guarantee, and ``lint-imports`` enforces on every commit, is that nothing in
``runtime``, ``company`` or ``realtime`` knows a domain exists. That is what keeps the option of
a domain registry open for the day a second business needs one.
"""
