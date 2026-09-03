"""Feedback and grievance rules.

The property that matters: a report of harm is never blocked. Requiring a farmer
to first identify which field or which diagnosis is exactly the friction that
stops the most important reports from arriving.
"""

import inspect

from app.api import feedback


class TestHarmReportingIsUnconditional:
    def test_harm_bypasses_the_reference_requirement(self):
        source = inspect.getsource(feedback.submit)
        assert 'if payload.verdict != "harmful":' in source, (
            "harm reports must skip the field_id/diagnosis_id requirement"
        )

    def test_a_bad_corrected_label_does_not_block_a_harm_report(self):
        source = inspect.getsource(feedback.submit)
        assert 'if payload.verdict == "harmful":' in source
        assert "corrected = None" in source

    def test_harm_is_escalated_not_just_counted(self):
        source = inspect.getsource(feedback.submit)
        assert 'urgent = payload.verdict == "harmful"' in source
        assert "escalated" in source

    def test_ordinary_feedback_still_needs_a_reference(self):
        source = inspect.getsource(feedback.submit)
        assert "diagnosis_id is required" in source
        assert "field_id is required" in source


class TestVerdicts:
    def test_the_four_verdicts_are_fixed(self):
        assert set(feedback.VERDICTS) == {"helpful", "unclear", "wrong", "harmful"}

    def test_harmful_is_a_distinct_verdict_from_wrong(self):
        """Wrong advice and advice that cost a farmer a crop need different
        handling, so they cannot share a bucket."""
        assert "wrong" in feedback.VERDICTS and "harmful" in feedback.VERDICTS


class TestOperatorAccess:
    def test_summary_is_restricted(self):
        source = inspect.getsource(feedback.summary)
        assert 'user.role not in ("extension", "admin")' in source

    def test_acknowledge_is_restricted(self):
        source = inspect.getsource(feedback.acknowledge)
        assert 'user.role not in ("extension", "admin")' in source

    def test_harm_is_ordered_first_in_triage(self):
        source = inspect.getsource(feedback.summary)
        assert "CASE verdict WHEN 'harmful' THEN 0" in source

    def test_urgent_items_are_listed_individually_not_counted(self):
        """A count is not an answer to somebody losing a crop."""
        source = inspect.getsource(feedback.summary)
        assert "needs_attention" in source


class TestCorrectionsAsData:
    def test_corrections_must_name_a_real_class(self):
        source = inspect.getsource(feedback.submit)
        assert "corrected_label must be a known disease class" in source

    def test_corrections_are_surfaced_as_ground_truth(self):
        source = inspect.getsource(feedback.summary)
        assert "field_corrections_collected" in source
