"""Contract tests for :mod:`reddit.hub.evidence`."""

from __future__ import annotations

import csv
import json
import logging
from io import StringIO
from pathlib import Path

import pytest
from yaml import safe_load

from reddit.core.config import Config
from reddit.core.utils import dump_object
from reddit.hub.card import SeedMetrics
from reddit.hub.evidence import (
    corpus_label_shares,
    evaluation_csv,
    seed_metrics,
    training_args_json,
    training_config_extract,
)


def write_dump(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as f:
        for record in records:
            f.write(dump_object(record))


def record(seed: int, method: str, f1: float) -> dict[str, object]:
    return {
        "seed": seed,
        "finetuning_method": method,
        "accuracy": f1,
        "f1_weighted": f1,
        "roc_auc": 0.8,
        "runtime": 1.0,
    }


class TestEvidenceModule:
    """Metric dumps, configuration extract and training arguments."""

    @pytest.fixture
    def dumps(self, bootstrapped_config: Config) -> Config:
        """Two run dates of ``tiny_llm`` dumps: seed 11 rerun on the second date."""
        output = bootstrapped_config.paths.output_dir
        first, second = output / "llm_family_20240101", output / "llm_family_20240102"
        write_dump(
            first / "dist_tiny_llm_test_metrics.jsonl",
            [record(11, "qdora", 0.5), record(22, "qdora", 0.6), record(22, "xqdora", 0.9)],
        )
        write_dump(first / "dist_tiny_llm_train_metrics.jsonl", [record(11, "qdora", 0.55)])
        write_dump(second / "dist_tiny_llm_test_metrics.jsonl", [record(11, "qdora", 0.7)])
        return bootstrapped_config

    @pytest.mark.unit
    class TestUnits:
        def test_seed_metrics_keep_one_method_sorted_by_seed(self, dumps: Config) -> None:
            seeds = seed_metrics(dumps, "tiny_llm", "qdora")

            assert [s.seed for s in seeds] == [11, 22]
            assert all("runtime" not in s.test for s in seeds)

        def test_a_rerun_seed_keeps_its_latest_record(self, dumps: Config) -> None:
            seeds = seed_metrics(dumps, "tiny_llm", "qdora")

            assert seeds[0].test["f1_weighted"] == pytest.approx(0.7)

        def test_validation_metrics_are_empty_without_a_train_record(self, dumps: Config) -> None:
            seeds = seed_metrics(dumps, "tiny_llm", "qdora")

            assert seeds[0].validation["f1_weighted"] == pytest.approx(0.55)
            assert seeds[1].validation == {}

        def test_no_dumps_means_no_seeds(self, bootstrapped_config: Config) -> None:
            assert seed_metrics(bootstrapped_config, "tiny_llm", "qdora") == ()

        def test_the_evaluation_csv_ranks_seeds_and_flags_the_selected_one(self) -> None:
            seeds = (SeedMetrics(1, {"f1_weighted": 0.5}, {}), SeedMetrics(2, {"f1_weighted": 0.9}, {}))

            rows = list(csv.DictReader(StringIO(evaluation_csv(seeds, "test", selected=1))))

            assert [row["seed"] for row in rows] == ["2", "1"]
            assert [row["selected"] for row in rows] == ["False", "True"]
            assert rows[0]["accuracy"] == ""

        def test_seeds_without_the_split_are_left_out_of_its_table(self) -> None:
            seeds = (
                SeedMetrics(1, {"f1_weighted": 0.5}, {}),
                SeedMetrics(2, {"f1_weighted": 0.9}, {"f1_weighted": 0.8}),
            )

            rows = list(csv.DictReader(StringIO(evaluation_csv(seeds, "validation", selected=2))))

            assert [row["seed"] for row in rows] == ["2"]

        def test_the_config_extract_records_what_governed_the_checkpoint(self, config: Config) -> None:
            extract = safe_load(training_config_extract(config, "llm", "acme/tiny-llm", "qdora", 22))

            assert extract["base_model"] == "acme/tiny-llm"
            assert extract["selected_seed"] == 22
            assert extract["finetuning_method"] == "qdora"
            assert extract["training"]["arguments"]["seed"] == 42
            assert extract["training"]["seeds"] == config.training.seeds
            assert "path" not in extract["dataset"]
            assert "gradient_checkpointing" in extract["training"]

        def test_the_encoder_extract_carries_the_bert_block_instead(self, config: Config) -> None:
            extract = safe_load(training_config_extract(config, "bert", "acme/tiny-bert", "-", 11))

            assert extract["training"]["bert"]["num_train_epochs"] == 15
            assert "learning_rate" not in extract["training"]

        def test_training_args_are_none_without_the_pickle(self, tmp_path: Path) -> None:
            assert training_args_json(tmp_path) is None

        def test_corpus_shares_come_from_the_labelled_csvs(self, bootstrapped_config: Config) -> None:
            labels = bootstrapped_config.paths.labels_dir
            (labels / "tiny_llm_submissions_qdora_predicted_labels.csv").write_text(
                "id_sub,tiny_llm_qdora_label\n1,up\n2,up\n3,down\n4,neutral\n", encoding="utf-8"
            )
            (labels / "tiny_bert_comments_predicted_labels.csv").write_text(
                "id_com,tiny_bert_label\n1,neutral\n", encoding="utf-8"
            )

            llm = corpus_label_shares(bootstrapped_config, "tiny_llm", "qdora")
            bert = corpus_label_shares(bootstrapped_config, "tiny_bert", "-")

            assert llm == {"submissions": {"up": 0.5, "down": 0.25, "neutral": 0.25}}
            assert bert == {"comments": {"neutral": 1.0}}

        def test_a_checkpoint_never_run_over_the_corpus_has_no_shares(self, bootstrapped_config: Config) -> None:
            assert corpus_label_shares(bootstrapped_config, "tiny_llm", "qdora") == {}

    @pytest.mark.integration
    class TestIntegration:
        def test_training_args_are_published_without_local_paths(self, tmp_path: Path) -> None:
            torch = pytest.importorskip("torch")
            from transformers import TrainingArguments  # noqa: PLC0415 - heavy import, after the skip

            checkpoint = tmp_path / "ckpt"
            checkpoint.mkdir()
            arguments = TrainingArguments(output_dir=str(tmp_path / "out"), report_to="none", learning_rate=3e-4)
            torch.save(arguments, checkpoint / "training_args.bin")

            payload = json.loads(training_args_json(checkpoint) or "null")

            assert payload["output_dir"] is None
            assert payload["learning_rate"] == pytest.approx(3e-4)

        def test_an_unreadable_pickle_is_skipped_with_a_warning(
            self, tmp_path: Path, caplog: pytest.LogCaptureFixture
        ) -> None:
            pytest.importorskip("torch")
            (tmp_path / "training_args.bin").write_text("not a pickle", encoding="utf-8")

            with caplog.at_level(logging.WARNING):
                assert training_args_json(tmp_path) is None
            assert "training_args.json" in caplog.text
