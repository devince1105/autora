"""Autora — AI Autonomous Company runtime.

Layering (dependencies only point downward; see logs/3d-office/08_REPOSITORY_STRUCTURE.md):

    domains/*  ->  company  ->  runtime  ->  db / infra
    realtime   ->  company, runtime

Only ``autora.app`` may import every layer.
"""

__version__ = "0.0.1"
