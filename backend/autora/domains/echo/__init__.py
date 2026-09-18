"""Echo domain: the smallest real workflow (T-213, Phase 2 acceptance).

Three agents with three roles pass a note down a chain, ``echo_research`` -> ``echo_analyze``
-> ``echo_write``. Each writes one note with the ``echo_note`` tool, then reports it as
structured output that a validator checks against the database. Nothing is faked except the
model: ``simulation.respond`` plays it when ``MODEL_PROVIDER=fake``, and with a real provider
the same prompts work unchanged.

It exercises every Phase 2 piece: workflow DAG and hand-offs, task queue and leases, agent
runner, tool events with ``produced`` artifacts, policy, cost ledger, trace and activity. It
stays after Phase 2 as the demo and smoke workflow for the realtime and 3D office phases.
"""

from autora.domains.echo.workflow import (
    ROLES,
    TEMPLATE,
    register_behaviors,
    register_templates,
    register_tools,
    staff_company,
)

__all__ = [
    "ROLES",
    "TEMPLATE",
    "register_behaviors",
    "register_templates",
    "register_tools",
    "staff_company",
]
