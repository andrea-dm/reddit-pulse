"""Contract tests for :mod:`reddit.hub.card`.

The renderer is pure, so every assertion reads the Markdown it returns:
the YAML front matter the Hub indexes, and the sections a reviewer edits.
"""

from __future__ import annotations

from typing import Any

import pytest
from yaml import safe_load

from reddit.hub.card import (
    RETRAINING_NOTICE,
    CheckpointCard,
    LabelRow,
    SeedMetrics,
    base_model_display_name,
    render_model_card,
)

LABELS = (LabelRow(0, "down", -1), LabelRow(1, "neutral", 0), LabelRow(2, "up", 1))


def metrics(f1: float) -> dict[str, float]:
    return {
        "accuracy": f1,
        "f1_weighted": f1,
        "f1_macro": f1 - 0.05,
        "precision_macro": f1,
        "recall_macro": f1,
        "roc_auc": 0.8,
        "loss": 1.0,
    }


def make_card(**overrides: Any) -> CheckpointCard:
    """A QDoRA+ adapter card over three seeds, the middle one selected."""
    facts: dict[str, Any] = {
        "repo_id": "acme/reddit-pulse-tiny_llm-qdora",
        "model_name": "tiny_llm",
        "base_model": "acme/tiny-llm",
        "kind": "llm",
        "method": "qdora",
        "seed": 22,
        "labels": LABELS,
        "seeds": (
            SeedMetrics(11, metrics(0.7), metrics(0.75)),
            SeedMetrics(22, metrics(0.6), metrics(0.65)),
            SeedMetrics(33, metrics(0.5), {}),
        ),
        "hyperparameters": (("Learning rate", "1e-4, cosine decay"),),
        "gold_counts": {"neutral": 5, "up": 3, "down": 2},
        "split_shares": (0.71, 0.19, 0.10),
        "max_length": 1024,
        "base_license": "apache-2.0",
    }
    facts.update(overrides)
    return CheckpointCard(**facts)


def front_matter(card_text: str) -> dict[str, Any]:
    """The YAML block between the first two ``---`` fences."""
    return safe_load(card_text.split("---\n")[1])


class TestCardModule:
    """Model-card rendering."""

    @pytest.mark.unit
    class TestUnits:
        def test_the_front_matter_describes_an_adapter(self) -> None:
            meta = front_matter(render_model_card(make_card()))

            assert meta["library_name"] == "peft"
            assert meta["base_model"] == "acme/tiny-llm"
            assert meta["base_model_relation"] == "adapter"
            assert meta["license"] == "apache-2.0"
            assert meta["pipeline_tag"] == "text-classification"
            assert "qdora" in meta["tags"]

        def test_the_model_index_reports_the_selected_seed_test_metrics(self) -> None:
            meta = front_matter(render_model_card(make_card()))

            result = meta["model-index"][0]
            assert result["name"] == "Reddit-pulse tiny llm QDoRA+"
            reported = {m.get("name", m["type"]): m["value"] for m in result["results"][0]["metrics"]}
            assert reported["F1 (weighted)"] == pytest.approx(0.6)
            assert "seed 22" in result["results"][0]["dataset"]["name"]

        def test_a_missing_license_leaves_the_field_out(self) -> None:
            meta = front_matter(render_model_card(make_card(base_license=None)))

            assert "license" not in meta

        def test_an_encoder_card_uses_the_transformers_pipeline(self) -> None:
            text = render_model_card(make_card(kind="bert", method="-", repo_id="acme/reddit-pulse-tiny_bert"))
            meta = front_matter(text)

            assert meta["library_name"] == "transformers"
            assert meta["base_model_relation"] == "finetune"
            assert 'pipeline("text-classification", model="acme/reddit-pulse-tiny_bert")' in text
            assert "PeftModel" not in text

        def test_an_adapter_card_loads_the_base_model_then_the_adapter(self) -> None:
            text = render_model_card(make_card())

            assert 'AutoModelForSequenceClassification.from_pretrained(\n    "acme/tiny-llm"' in text
            assert "PeftModel.from_pretrained(base, name)" in text
            assert "max_length=1024" in text

        def test_the_labels_table_carries_the_signed_encodings(self) -> None:
            text = render_model_card(make_card())

            assert "| 0 | `down` | `-1` |" in text
            assert "| 2 | `up` | `+1` |" in text

        def test_seeds_are_ranked_by_test_f1_with_the_selected_one_marked(self) -> None:
            text = render_model_card(make_card())

            rows = [line for line in text.splitlines() if line.startswith(("| 11 ", "| 22 ", "| 33 "))]
            assert [row.split(" | ")[0] for row in rows] == ["| 11", "| 22", "| 33"]
            assert "**selected**" in rows[1]
            assert "**selected**" not in rows[0]

        def test_the_selected_seed_table_shows_validation_and_test(self) -> None:
            text = render_model_card(make_card())

            assert "| validation | 0.650 |" in text
            assert "| **test** | 0.600 |" in text

        def test_the_gold_set_table_sums_the_label_counts(self) -> None:
            text = render_model_card(make_card())

            assert "**10 Reddit submission titles**" in text
            assert "| neutral | 5 | 50.0 % |" in text
            assert "(7 / 2 / 1 titles)" in text

        def test_a_run_without_dumps_says_so_instead_of_printing_empty_tables(self) -> None:
            text = render_model_card(make_card(seeds=()))

            assert "No per-seed metrics dump was found" in text
            assert "| seed |" not in text

        def test_a_selected_seed_absent_from_the_dumps_is_reported(self) -> None:
            card = make_card(seed=99)

            assert card.selected is None
            assert "Its metrics were not found" in render_model_card(card)

        def test_the_hyperparameter_rows_are_printed_verbatim(self) -> None:
            text = render_model_card(make_card())

            assert "| Learning rate | 1e-4, cosine decay |" in text

        def test_the_card_names_the_paper_and_the_repository(self) -> None:
            text = render_model_card(make_card())

            assert "10.32057/0.QEF.2026.1028" in text
            assert "@techreport{delmonaco2026reddit" in text
            assert text.endswith("\n")


class TestReferenceSections:
    """The sections that mirror the hand-published `reddit-pulse-bert` card."""

    @pytest.mark.unit
    class TestUnits:
        def test_the_citation_carries_the_article_and_the_working_paper(self) -> None:
            text = render_model_card(make_card())

            assert "@article{delmonaco2026reddit," in text
            assert "@techreport{delmonaco2026reddit_qef," in text
            assert "Journal of Applied Econometrics, forthcoming." in text

        def test_the_files_table_lists_every_published_file(self) -> None:
            text = render_model_card(
                make_card(
                    files=("adapter_model.safetensors", "config.json", "LICENSE.txt", "README.md"),
                    license_files=("LICENSE.txt",),
                )
            )

            assert "| `adapter_model.safetensors` | DoRA adapter weights" in text
            assert "| `LICENSE.txt` | license / use-policy notice of the base model" in text
            assert "| `README.md` | this card |" in text

        def test_a_file_listed_twice_is_printed_once(self) -> None:
            text = render_model_card(make_card(files=("README.md", "config.json", "README.md")))

            assert text.count("| `README.md` | this card |") == 1

        def test_the_license_section_names_the_base_terms(self) -> None:
            gemma = render_model_card(make_card(base_license="gemma"))
            llama = render_model_card(make_card(base_license="llama3.2", license_files=("LICENSE.txt",)))
            unknown = render_model_card(make_card(base_license=None))

            assert "Gemma Terms of Use" in gemma
            assert "ships no license file" in gemma
            assert "Llama 3.2 Community License" in llama
            assert "[`LICENSE.txt`](LICENSE.txt)" in llama
            assert "declares no license on its card" in unknown
            assert "views expressed in the paper" in unknown

        def test_the_corpus_section_appears_only_when_the_corpus_was_labelled(self) -> None:
            without = render_model_card(make_card())
            with_shares = render_model_card(
                make_card(corpus_shares={"submissions": {"up": 0.436, "neutral": 0.42, "down": 0.144}})
            )

            assert "## Corpus labelling in the paper" not in without
            assert "| submissions | 14.4 % | 42.0 % | 43.6 % |" in with_shares

        def test_the_reproducing_section_names_the_model(self) -> None:
            text = render_model_card(make_card())

            assert "reddit run --model tiny_llm --gpu 0" in text
            assert "reddit upload --model tiny_llm" in text


class TestTitles:
    """The human-readable card title: ``Reddit-pulse <base model> <method>``."""

    @pytest.mark.unit
    class TestUnits:
        def test_an_adapter_title_spells_the_base_model_and_the_method(self) -> None:
            card = make_card(base_model="google/gemma-2-2b", method="xqdora")

            assert card.title == "Reddit-pulse Gemma 2 2B xQDoRA+"
            assert "\n# Reddit-pulse Gemma 2 2B xQDoRA+\n" in render_model_card(card)

        def test_an_encoder_title_carries_no_method(self) -> None:
            card = make_card(
                repo_id="acme/reddit-pulse-inflabert",
                kind="bert",
                method="-",
                base_model="MAPAi/InflaBERT",
                max_length=512,
            )

            assert card.title == "Reddit-pulse InflaBERT"

        def test_the_model_index_uses_the_title(self) -> None:
            entry = front_matter(render_model_card(make_card(base_model="Qwen/Qwen2.5-0.5B")))["model-index"][0]

            assert entry["name"] == "Reddit-pulse Qwen2.5 0.5B QDoRA+"

        def test_an_unlisted_base_model_gets_a_readable_fallback(self) -> None:
            assert base_model_display_name("acme/tiny-llm") == "tiny llm"
            assert make_card().title == "Reddit-pulse tiny llm QDoRA+"

        def test_the_retraining_notice_sits_right_under_the_title(self) -> None:
            text = render_model_card(make_card(base_model="google/gemma-2-2b"))

            assert "# Reddit-pulse Gemma 2 2B QDoRA+\n\n" + RETRAINING_NOTICE + "\n\n**Reddit-pulse" in text
            assert "may differ from the ones reported in the paper" in RETRAINING_NOTICE
