import json
import os
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

# Groq() refuses to construct without a key; no request is ever sent in these tests.
os.environ.setdefault("GROQ_API_KEY", "dummy")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import analyzer  # noqa: E402

JOB_TEXT = "Acme | Senior Python Engineer | Remote (US) | $150k | hiring now, visa available"


class FakeCompletions:
    """Stands in for client.chat.completions; returns queued response texts."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        content = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        message = SimpleNamespace(content=content)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(total_tokens=600),
        )


@pytest.fixture
def fake_client(monkeypatch):
    def install(*responses):
        completions = FakeCompletions(responses)
        monkeypatch.setattr(analyzer, "client", SimpleNamespace(chat=SimpleNamespace(completions=completions)))
        return completions

    monkeypatch.setattr(analyzer.time, "sleep", lambda seconds: None)
    return install


def comment(hn_id, text=JOB_TEXT):
    return {"id": hn_id, "time": 1790000000, "text": text}


def run(tmp_path, comments, known=(), skipped=()):
    return analyzer.JobAnalyzer().process_comments(
        comments,
        set(known),
        pd.DataFrame(),
        out_path=str(tmp_path / "jobs.csv"),
        skipped_ids=set(skipped),
        skipped_path=str(tmp_path / "skipped.csv"),
    )


def test_list_response_is_skipped_without_retry(fake_client, tmp_path):
    completions = fake_client(json.dumps([{"company": "A"}, {"company": "B"}]))

    results, skipped, stop_reason, stats = run(tmp_path, [comment(1)])

    assert results == []
    assert [s["hn_id"] for s in skipped] == ["1"]
    assert skipped[0]["reason"] == "multiple_jobs"
    assert stop_reason == "complete"
    assert stats["skipped"] == 1 and stats["failed"] == 0
    assert completions.calls == 1


def test_non_object_response_is_skipped(fake_client, tmp_path):
    fake_client('"just a string"')

    _, skipped, _, _ = run(tmp_path, [comment(1)])

    assert skipped[0]["reason"] == "not_an_object"


def test_previously_skipped_post_is_never_sent(fake_client, tmp_path):
    completions = fake_client(json.dumps({"company": "Acme"}))

    results, skipped, _, stats = run(tmp_path, [comment(1)], skipped={"1"})

    assert completions.calls == 0
    assert results == [] and skipped == []
    assert stats["previously_skipped"] == 1 and stats["pending"] == 0


def test_object_response_still_extracts(fake_client, tmp_path):
    fake_client(json.dumps({"company": "Acme", "tech_stack": ["Python", "Backend"], "salary_year_usd": 150}))

    results, skipped, _, _ = run(tmp_path, [comment(7)])

    assert skipped == []
    assert results[0]["hn_id"] == "7"
    assert results[0]["salary_year_usd"] == 150000
    assert results[0]["tech_stack"] == ["Python"]


def test_invalid_json_still_counts_as_failed(fake_client, tmp_path):
    fake_client("not json at all")

    results, skipped, stop_reason, stats = run(tmp_path, [comment(1)])

    assert results == [] and skipped == []
    assert stats["failed"] == 1
    assert analyzer.run_failed(stats, results, skipped, stop_reason)


def test_run_failed_only_when_nothing_was_resolved():
    stats = {"pending": 1}
    assert not analyzer.run_failed(stats, [], [{"hn_id": "1"}], "complete")
    assert not analyzer.run_failed(stats, [{"hn_id": "1"}], [], "complete")
    assert not analyzer.run_failed(stats, [], [], "budget")
    assert not analyzer.run_failed({"pending": 0}, [], [], "complete")


def test_skip_list_round_trip_and_dedupe(tmp_path):
    path = str(tmp_path / "skipped.csv")
    assert analyzer.load_skipped(path).empty

    analyzer.save_skipped(analyzer.load_skipped(path), [{"hn_id": "1", "reason": "multiple_jobs", "first_seen": 1}], path)
    analyzer.save_skipped(
        analyzer.load_skipped(path),
        [{"hn_id": "1", "reason": "multiple_jobs", "first_seen": 2}, {"hn_id": "2", "reason": "not_an_object", "first_seen": 3}],
        path,
    )

    saved = analyzer.load_skipped(path)
    assert sorted(saved["hn_id"]) == ["1", "2"]
    # The first sighting is kept for a post skipped twice.
    assert int(saved.loc[saved["hn_id"] == "1", "first_seen"].iloc[0]) == 1
