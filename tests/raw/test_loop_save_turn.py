from nanobot.agent.context import ContextBuilder
from nanobot.agent.loop import AgentLoop
from nanobot.session.manager import Session


def _mk_loop() -> AgentLoop:
    return AgentLoop.__new__(AgentLoop)


def test_save_turn_skips_multimodal_user_when_only_runtime_context() -> None:
    loop = _mk_loop()
    session = Session(key="test:runtime-only")
    runtime = ContextBuilder._RUNTIME_CONTEXT_TAG + "\nCurrent Time: now (UTC)"

    loop._save_turn(
        session,
        [{"role": "user", "content": [{"type": "text", "text": runtime}]}],
        skip=0,
    )
    assert session.messages == []


def test_save_turn_keeps_image_placeholder_after_runtime_strip() -> None:
    loop = _mk_loop()
    session = Session(key="test:image")
    runtime = ContextBuilder._RUNTIME_CONTEXT_TAG + "\nCurrent Time: now (UTC)"

    loop._save_turn(
        session,
        [{
            "role": "user",
            "content": [
                {"type": "text", "text": runtime},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
        }],
        skip=0,
    )
    assert session.messages[0]["content"] == [{"type": "text", "text": "[image]"}]


def test_save_turn_keeps_full_tool_result_in_session() -> None:
    loop = _mk_loop()
    session = Session(key="test:tool")
    full_result = "x" * 600

    loop._save_turn(
        session,
        [{
            "role": "tool",
            "tool_call_id": "call_123",
            "name": "read_file",
            "content": full_result,
        }],
        skip=0,
    )

    assert session.messages[0]["content"] == full_result
