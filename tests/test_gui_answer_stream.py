from __future__ import annotations

from lucode.gui.answer_stream import AnswerStreamState


def test_answer_stream_appends_delta_text():
    state = AnswerStreamState()

    assert state.append_delta("Hello") == "Hello"
    assert state.append_delta(" world") == "Hello world"
    assert state.has_streamed is True


def test_answer_stream_final_output_replaces_streamed_text():
    state = AnswerStreamState()
    state.append_delta("Partial")

    assert state.finalize("Partial final") == "Partial final"
    assert state.text == "Partial final"


def test_answer_stream_ignores_empty_delta_and_empty_final():
    state = AnswerStreamState()

    assert state.append_delta("") == ""
    assert state.has_streamed is False
    assert state.finalize("") == ""
    assert state.has_streamed is False
