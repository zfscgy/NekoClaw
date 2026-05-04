from nekoclaw.providers.base import StreamDelta
from nekoclaw.session.manager import Session, SessionManager


def test_delete_session_moves_associated_subagent_sessions(tmp_path):
    manager = SessionManager(tmp_path)

    parent = Session(
        key="nekochat:chat-1",
        messages=[
            StreamDelta(
                type="subagent_ref",
                content={"session_id": "subagent:referenced", "label": "Referenced"},
            )
        ],
    )
    manager.save(parent)

    referenced = Session(
        key="subagent:referenced",
        metadata={"parent_channel": "nekochat", "parent_chat_id": "chat-1"},
    )
    unrelated = Session(
        key="subagent:unrelated",
        metadata={"parent_channel": "nekochat", "parent_chat_id": "chat-2"},
    )
    manager.save(referenced)
    manager.save(unrelated)

    moved_to = manager.delete_session("nekochat:chat-1")

    assert moved_to == tmp_path / "sessions" / "bin" / "nekochat_chat-1.jsonl"
    assert not manager._get_session_path("nekochat:chat-1").exists()
    assert not manager._get_session_path("subagent:referenced").exists()
    assert manager._get_session_path("subagent:unrelated").exists()

    assert (tmp_path / "sessions" / "bin" / "subagent_referenced.jsonl").exists()
