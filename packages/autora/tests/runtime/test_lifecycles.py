"""T-201: Task, AgentRun and WorkflowRun lifecycles."""

import pytest

from autora.db.models import AGENT_RUN_TERMINAL, AgentRunState, TaskState, WorkflowRunState
from autora.runtime.lifecycles import AGENT_RUN_FSM, TASK_FSM, WORKFLOW_RUN_FSM

MACHINES = [TASK_FSM, AGENT_RUN_FSM, WORKFLOW_RUN_FSM]


def _reachable(machine, start):
    seen, frontier = {start}, [start]
    while frontier:
        for target in machine.allowed_from(frontier.pop()):
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    return seen


@pytest.mark.parametrize("machine", MACHINES, ids=lambda m: m.entity_type)
def test_every_state_reachable_from_initial(machine):
    assert _reachable(machine, machine.initial) == set(machine.states)


@pytest.mark.parametrize("machine", MACHINES, ids=lambda m: m.entity_type)
def test_every_state_can_terminate(machine):
    """No lifecycle can get stuck: a terminal state is reachable from everywhere."""
    terminal = machine.terminal_states()
    for state in machine.states:
        assert _reachable(machine, state) & terminal, f"{state} cannot reach a terminal state"


@pytest.mark.parametrize("machine", MACHINES, ids=lambda m: m.entity_type)
def test_cancel_or_abort_possible_from_every_live_state(machine):
    stop = {"task": "CANCELLED", "agent_run": "ABORTED", "workflow_run": "CANCELLED"}[
        machine.entity_type
    ]
    for state in set(machine.states) - machine.terminal_states():
        assert machine.can(state, stop), f"{state} cannot be stopped"


def test_terminal_states():
    assert TASK_FSM.terminal_states() == {
        TaskState.SUCCEEDED,
        TaskState.FAILED,
        TaskState.CANCELLED,
    }
    assert AGENT_RUN_FSM.terminal_states() == set(AGENT_RUN_TERMINAL)
    assert WORKFLOW_RUN_FSM.terminal_states() == {
        WorkflowRunState.SUCCEEDED,
        WorkflowRunState.FAILED,
        WorkflowRunState.CANCELLED,
    }


@pytest.mark.parametrize(
    ("source", "target", "allowed"),
    [
        # retry and expired lease return to the queue; FAILED is final only
        (TaskState.RUNNING, TaskState.READY, True),
        (TaskState.FAILED, TaskState.READY, False),
        # approval releases the worker, then the task is re-claimed
        (TaskState.RUNNING, TaskState.WAITING_APPROVAL, True),
        (TaskState.WAITING_APPROVAL, TaskState.RUNNING, False),
        (TaskState.WAITING_APPROVAL, TaskState.READY, True),
        # human/service node completes directly on approval
        (TaskState.READY, TaskState.WAITING_APPROVAL, True),
        (TaskState.WAITING_APPROVAL, TaskState.SUCCEEDED, True),
        # dependencies must be satisfied before running
        (TaskState.PENDING, TaskState.RUNNING, False),
        (TaskState.BLOCKED_BUDGET, TaskState.READY, True),
        (TaskState.BLOCKED_BUDGET, TaskState.FAILED, True),
    ],
)
def test_task_transitions(source, target, allowed):
    assert TASK_FSM.can(source, target) is allowed


@pytest.mark.parametrize(
    ("source", "target", "allowed"),
    [
        (AgentRunState.CREATED, AgentRunState.EVALUATING, False),
        (AgentRunState.RUNNING, AgentRunState.COMPLETED, False),  # must be evaluated first
        (AgentRunState.EVALUATING, AgentRunState.COMPLETED, True),
        (AgentRunState.EVALUATING, AgentRunState.REPAIRING, True),
        (AgentRunState.REPAIRING, AgentRunState.RUNNING, True),
        (AgentRunState.WAITING_APPROVAL, AgentRunState.RUNNING, True),
    ],
)
def test_agent_run_transitions(source, target, allowed):
    assert AGENT_RUN_FSM.can(source, target) is allowed
