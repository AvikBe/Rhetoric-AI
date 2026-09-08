"""Stage 2: the repair pass, the validators, and the retry loop's behaviour."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from rhetoric.plan import PaperPlan, _reasons, build_prompts, generate, repair
from tests.conftest import figure


class TestRepairIsMechanicalOnly:
    def test_snake_case_keys_are_normalised_everywhere(self, plan_dict: dict) -> None:
        """The key and every marker referencing it must move together."""
        plan_dict["citations"][0]["key"] = "adeyemi_2022"
        plan_dict["abstract_beats"] = ["Holds [[adeyemi_2022]]."]
        fixed, notes = repair(plan_dict, "claim")

        assert fixed["citations"][0]["key"] == "adeyemi2022"
        assert fixed["abstract_beats"][0] == "Holds [[adeyemi2022]]."
        assert any("normalised" in n for n in notes)

    def test_figure_labels_are_slugged(self, plan_dict: dict) -> None:
        plan_dict["sections"][2]["figure"]["label"] = "SCI By Form_A"
        fixed, notes = repair(plan_dict, "claim")

        assert fixed["sections"][2]["figure"]["label"] == "sci-by-form-a"
        assert any("slugged" in n for n in notes)

    def test_undefined_keys_are_left_alone(self, plan_dict: dict) -> None:
        """Regression: deleting the marker mangled the sentence.

        The old repair pass dropped markers for undefined keys, leaving
        "Follows from ." and "by [[a]],, the effect holds". Semantic errors
        belong to validation, so the model rewrites its own sentence.
        """
        plan_dict["abstract_beats"] = ["As shown by [[adeyemi2022]], and [[ghost2020]], it holds."]
        fixed, _ = repair(plan_dict, "claim")

        assert fixed["abstract_beats"][0] == (
            "As shown by [[adeyemi2022]], and [[ghost2020]], it holds."
        )
        with pytest.raises(ValidationError, match="undefined keys"):
            PaperPlan.model_validate(fixed)

    def test_claim_is_injected(self, plan_dict: dict) -> None:
        """The claim comes from the CLI, never from the model."""
        assert repair(plan_dict, "A bagel is a doughnut")[0]["claim"] == "A bagel is a doughnut"


class TestPlanValidators:
    def test_duplicate_figure_labels(self, plan_dict: dict) -> None:
        """Two figures sharing a label overwrite each other's PNG silently."""
        plan_dict["sections"][1]["figure"] = figure(
            "bar_errorbar", "sci-by-form", ["a", "b"], [1.0, 2.0]
        )
        with pytest.raises(ValidationError, match="duplicate figure labels"):
            PaperPlan.model_validate(plan_dict)

    def test_missing_required_section(self, plan_dict: dict) -> None:
        plan_dict["sections"] = [s for s in plan_dict["sections"] if s["heading"] != "Methods"]
        with pytest.raises(ValidationError, match="missing required section"):
            PaperPlan.model_validate(plan_dict)

    def test_headings_are_an_enum(self, plan_dict: dict) -> None:
        """The grammar makes an invented "References" section unrepresentable."""
        plan_dict["sections"][0]["heading"] = "References"
        with pytest.raises(ValidationError):
            PaperPlan.model_validate(plan_dict)

    def test_sections_are_sorted_canonically(self, plan_dict: dict) -> None:
        plan_dict["sections"].reverse()
        plan = PaperPlan.model_validate(plan_dict)
        assert [s.heading for s in plan.sections] == [
            "Introduction", "Methods", "Results", "Conclusion"
        ]

    def test_exemplar_leakage_is_rejected(self, plan_dict: dict) -> None:
        """qwen3:8b returned the exemplar's authors and sample size verbatim."""
        plan_dict["authors"][0]["name"] = "T. Lindqvist"
        plan_dict["dataset"]["n"] = 1247
        with pytest.raises(ValidationError, match="copied from the example"):
            PaperPlan.model_validate(plan_dict)

    def test_latex_in_beats_is_rejected(self, plan_dict: dict) -> None:
        plan_dict["abstract_beats"] = [r"Emphasise \textbf{this} point."]
        with pytest.raises(ValidationError, match="contains LaTeX"):
            PaperPlan.model_validate(plan_dict)


class TestRetryLoop:
    def test_feedback_does_not_accumulate(self, plan_dict: dict, monkeypatch) -> None:
        """Regression: appending each error grew the prompt every attempt.

        By attempt three that was enough to push a local runtime into an
        out-of-memory crash, and it buried the current error under stale ones.
        """
        broken = copy.deepcopy(plan_dict)
        broken["authors"][0]["name"] = "T. Lindqvist"  # fails leakage
        seen: list[str] = []

        def fake(cfg, system, user, schema, name="response"):
            seen.append(user)
            return copy.deepcopy(broken) if len(seen) < 3 else copy.deepcopy(plan_dict)

        monkeypatch.setattr("rhetoric.plan.complete_json", fake)
        generate("A hot dog is a sandwich", _cfg(), attempts=3)

        marker = "A previous attempt was rejected"
        assert len(seen) == 3
        assert seen[0].count(marker) == 0, "the first attempt carries no feedback"
        # The decisive assertion: each retry carries exactly one error block, so
        # the third prompt is not the second prompt plus another one.
        assert seen[1].count(marker) == 1
        assert seen[2].count(marker) == 1, "feedback accumulated across retries"
        assert len(seen[2]) == pytest.approx(len(seen[1]), rel=0.05)

    def test_feedback_is_concise(self, plan_dict: dict) -> None:
        """Pydantic's full rendering embeds the entire input JSON."""
        plan_dict["authors"][0]["name"] = "T. Lindqvist"
        try:
            PaperPlan.model_validate(plan_dict)
        except ValidationError as exc:
            reasons = _reasons(exc)
            assert "copied from the example" in reasons
            assert len(reasons) < len(str(exc))
            assert "input_value" not in reasons


class TestPrompts:
    def test_no_unfilled_placeholders(self) -> None:
        system, user = build_prompts("A hot dog is a sandwich")
        assert "{{" not in system + user
        assert "A hot dog is a sandwich" in user


def _cfg():
    from rhetoric.llm import ModelConfig

    return ModelConfig(
        provider="openrouter", model="fake", temperature=0.9, max_tokens=100,
        num_ctx=8192, timeout=60, think=False, base_url="http://x", api_key="k",
    )
