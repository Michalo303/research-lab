import json

import pytest

from hermes_knowledge.feedback import apply_feedback, feedback_delta


def _event(event_id="event-1", **overrides):
    event = {
        "event_id": event_id,
        "used_note_ids": ["note-1111111111111111"],
        "baseline_wf_pass_rate": 0.42,
        "wf_pass_rate": 0.58,
        "baseline_max_drawdown": 0.22,
        "max_drawdown": 0.13,
        "gate_passed": False,
    }
    event.update(overrides)
    return event


def test_feedback_delta_rewards_wf_and_drawdown_improvement_deterministically():
    assert feedback_delta(_event()) == pytest.approx(4.1)
    assert feedback_delta(
        _event(
            wf_pass_rate=0.30,
            max_drawdown=0.30,
            gate_passed=False,
        )
    ) == pytest.approx(-12.2)


def test_feedback_missing_metrics_contribute_zero_and_gate_is_bounded():
    assert feedback_delta(
        {
            "event_id": "event-1",
            "used_note_ids": ["note-1111111111111111"],
            "gate_passed": True,
        }
    ) == 5.0


def test_apply_feedback_deduplicates_events_and_updates_note_and_book_overlays(tmp_path):
    event_path = tmp_path / "feedback" / "note_feedback.jsonl"
    priorities_path = tmp_path / "feedback" / "priorities.json"
    note_to_book = {"note-1111111111111111": "book-aaaaaaaaaaaa"}

    first = apply_feedback(
        [_event()],
        note_to_book=note_to_book,
        event_path=event_path,
        priorities_path=priorities_path,
    )
    second = apply_feedback(
        [_event()],
        note_to_book=note_to_book,
        event_path=event_path,
        priorities_path=priorities_path,
    )

    assert first.accepted == 1
    assert second.duplicates == 1
    payload = json.loads(priorities_path.read_text(encoding="utf-8"))
    assert payload["notes"]["note-1111111111111111"] == pytest.approx(4.1)
    assert payload["books"]["book-aaaaaaaaaaaa"] == pytest.approx(4.1)
    assert len(event_path.read_text(encoding="utf-8").splitlines()) == 1


def test_feedback_priorities_are_clamped(tmp_path):
    events = [
        _event(f"event-{index}", wf_pass_rate=1.0, max_drawdown=0.0, gate_passed=True)
        for index in range(10)
    ]
    priorities = tmp_path / "feedback" / "priorities.json"

    apply_feedback(
        events,
        note_to_book={"note-1111111111111111": "book-aaaaaaaaaaaa"},
        event_path=tmp_path / "feedback" / "events.jsonl",
        priorities_path=priorities,
    )

    payload = json.loads(priorities.read_text(encoding="utf-8"))
    assert payload["notes"]["note-1111111111111111"] == 50.0
    assert payload["books"]["book-aaaaaaaaaaaa"] == 50.0


def test_malformed_feedback_is_rejected_without_priority_change(tmp_path):
    priorities = tmp_path / "feedback" / "priorities.json"
    summary = apply_feedback(
        [_event(used_note_ids=["bad-id"])],
        note_to_book={},
        event_path=tmp_path / "feedback" / "events.jsonl",
        priorities_path=priorities,
    )

    assert summary.rejected == 1
    assert not priorities.exists()
