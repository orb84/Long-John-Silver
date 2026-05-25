"""Round 56 regressions: deterministic agent chat stays persona-based."""

from pathlib import Path

from src.ai.chat_presenter import AgentChatPresenter


def test_download_progress_is_persona_based_and_useful() -> None:
    presenter = AgentChatPresenter("default")

    message = presenter.progress(
        "download the missing episodes from season 5 of For All Mankind",
        tick=0,
    )

    assert "Captain" in message
    assert "manifest" in message.lower() or "release" in message.lower()
    assert "download" not in message.lower() or "releases" in message.lower()


def test_batch_queue_result_reports_units_titles_and_fallbacks() -> None:
    presenter = AgentChatPresenter("default")

    message = presenter.batch_queue_result(
        item_name="For All Mankind",
        queued=[
            {"season": 5, "episode": 4, "title": "For.All.Mankind.S05E04.ITA.1080p"},
            {"season": 5, "episode": 5, "title": "For.All.Mankind.S05E05.ITA.1080p"},
        ],
        failed=[{"season": 5, "episode": 6, "title": "For.All.Mankind.S05E06.ITA.1080p"}],
        fallback_count=1,
    )

    assert message.startswith("Aye Captain")
    assert "For All Mankind" in message
    assert "S05E04" in message and "S05E05" in message
    assert "For.All.Mankind.S05E04.ITA.1080p" in message
    assert "S05E06" in message
    assert "not** mark" in message or "not mark" in message.lower()
    assert "alternate" in message.lower()


def test_frontend_handles_status_frames_without_final_answer_bubble_reuse() -> None:
    chat = Path("src/web/static/js/components/chatController.js").read_text()
    app = Path("src/web/app.py").read_text()

    assert "data.type === 'status'" in chat
    assert "_appendMsg('status'" in chat
    assert "this.assistantBubble = null" not in chat.split("data.type === 'status'", 1)[1].split("data.type === 'done'", 1)[0]
    assert "_stream_chat_with_progress" in app
    assert '"type": "status"' in app
