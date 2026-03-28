import json

from nanobot.channels.nanochat import NanochatChannel


def test_session_to_ui_keeps_assistant_text_alongside_tool_calls() -> None:
    messages = [{
        "role": "assistant",
        "content": "I checked that for you.",
        "tool_calls": [{
            "id": "call_123",
            "type": "function",
            "function": {
                "name": "read_file",
                "arguments": json.dumps({"path": "README.md"}),
            },
        }],
    }]

    ui = NanochatChannel._session_to_ui("conv-1", messages)

    assert ui == [
        {
            "type": "tool_call",
            "role": "assistant",
            "content": 'read_file({"path": "README.md"})',
            "media": [],
            "conversation_id": "conv-1",
        },
        {
            "type": "content",
            "role": "assistant",
            "content": "I checked that for you.",
            "media": [],
            "conversation_id": "conv-1",
        },
    ]


def test_session_to_ui_preserves_saved_content_then_tool_call_order() -> None:
    messages = [{
        "role": "assistant",
        "content": "I checked that for you.",
        "tool_calls": [{
            "id": "call_123",
            "type": "function",
            "function": {
                "name": "read_file",
                "arguments": json.dumps({"path": "README.md"}),
            },
        }],
        "_ui_segments": ["content", "tool_calls"],
    }]

    ui = NanochatChannel._session_to_ui("conv-1", messages)

    assert ui == [
        {
            "type": "content",
            "role": "assistant",
            "content": "I checked that for you.",
            "media": [],
            "conversation_id": "conv-1",
        },
        {
            "type": "tool_call",
            "role": "assistant",
            "content": 'read_file({"path": "README.md"})',
            "media": [],
            "conversation_id": "conv-1",
        },
    ]


def test_session_to_ui_rewrites_message_tool_call_media_to_public_urls(monkeypatch) -> None:
    monkeypatch.setattr(
        NanochatChannel,
        "_media_to_urls",
        staticmethod(lambda paths: ["/file/token123?name=%E4%B8%AD%E6%96%87.png" if p == "C:/tmp/中文.png" else p for p in paths]),
    )

    messages = [{
        "role": "assistant",
        "tool_calls": [{
            "id": "call_123",
            "type": "function",
            "function": {
                "name": "message",
                "arguments": json.dumps({
                    "content": "Sent via tool",
                    "media": [
                        "C:/tmp/中文.png",
                        "https://example.com/a.png",
                    ],
                }),
            },
        }],
    }]

    ui = NanochatChannel._session_to_ui("conv-1", messages)

    assert ui == [
        {
            "type": "tool_call",
            "role": "assistant",
            "content": 'message({"content": "Sent via tool", "media": ["/file/token123?name=%E4%B8%AD%E6%96%87.png", "https://example.com/a.png"]})',
            "media": [],
            "conversation_id": "conv-1",
        },
    ]


def test_session_to_ui_message_tool_media_stays_in_tool_call(monkeypatch) -> None:
    monkeypatch.setattr(
        NanochatChannel,
        "_media_to_urls",
        staticmethod(lambda paths: [f"/file/token{idx}?name=file{idx}.txt" for idx, _ in enumerate(paths, start=1)]),
    )

    messages = [{
        "role": "assistant",
        "content": "Here is the file.",
        "tool_calls": [{
            "id": "call_123",
            "type": "function",
            "function": {
                "name": "message",
                "arguments": json.dumps({
                    "content": "Here is the file.",
                    "media": ["D:/tmp/report.txt"],
                }),
            },
        }],
        "_ui_segments": ["content", "tool_calls"],
    }]

    ui = NanochatChannel._session_to_ui("conv-1", messages)

    assert ui == [
        {
            "type": "content",
            "role": "assistant",
            "content": "Here is the file.",
            "media": [],
            "conversation_id": "conv-1",
        },
        {
            "type": "tool_call",
            "role": "assistant",
            "content": 'message({"content": "Here is the file.", "media": ["/file/token1?name=file1.txt"]})',
            "media": [],
            "conversation_id": "conv-1",
        },
    ]


def test_cur_round_replay_preserves_content_tool_call_order() -> None:
    cur = {}

    NanochatChannel._append_cur_round_text_item(cur, "content", "I checked that for you.")
    NanochatChannel._upsert_cur_round_tool_item(
        cur,
        0,
        "read_file",
        json.dumps({"path": "README.md"}),
    )
    NanochatChannel._append_cur_round_text_item(cur, "content", " Next, I will summarize it.")

    payloads = NanochatChannel._cur_round_to_replay_payloads("conv-1", cur)

    assert payloads == [
        {
            "type": "stream_content_delta",
            "role": "assistant",
            "content": "I checked that for you.",
            "conversation_id": "conv-1",
        },
        {
            "type": "stream_tool_call_delta",
            "content": '{"index": 0, "name": "read_file", "arguments": "{\\"path\\": \\"README.md\\"}"}',
            "conversation_id": "conv-1",
        },
        {
            "type": "stream_content_delta",
            "role": "assistant",
            "content": " Next, I will summarize it.",
            "conversation_id": "conv-1",
        },
    ]
