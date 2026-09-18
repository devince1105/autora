"""Execution trace: what an agent run did, step by step (T-210).

``record_step`` writes an ``agent_steps`` row (large payloads go to the BlobStore);
``get_run_trace`` returns the run's real events joined with its steps. Nothing in a trace is
synthesized: every entry is an event row or a step row.
"""

from autora.runtime.trace.query import StepView, Trace, TraceEntry, get_run_trace
from autora.runtime.trace.recorder import record_step, step_blob_key

__all__ = ["StepView", "Trace", "TraceEntry", "get_run_trace", "record_step", "step_blob_key"]
