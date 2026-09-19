"""T-518: the simulated model's demo knobs, read from the task input like a model would."""

import uuid

from autora.domains.newsroom.simulation import respond
from autora.runtime.models.types import CallContext, Message, ModelRequest


def _request(params: str) -> ModelRequest:
    text = f'Task: 研究 (research)\nInput:\n{{"params": {params}}}\n\nStory: X\nStory id: s'
    return ModelRequest(
        capability="research_extraction",
        context=CallContext(company_id=uuid.uuid4(), role="researcher", task_name="research"),
        messages=[Message.user(text)],
    )


def test_the_demo_pace_slows_every_reply():
    assert respond(_request('{"demo": {"pace_seconds": 2.5}}')).delay_s == 2.5
    assert respond(_request('{"demo": {"pace_seconds": 999}}')).delay_s == 60  # capped
    assert respond(_request('{"demo": "nonsense"}')).delay_s == 0
    assert respond(_request("{}")).delay_s == 0
