"""Data access functions, one module per aggregate.

Plain async functions taking an ``AsyncSession``. They never commit: the caller owns the
transaction (``session_scope()`` in services, the command pipeline later). Agents never call
these directly; they go through tools and commands (logs/platform/03_AGENT_RUNTIME.md §1).
"""
