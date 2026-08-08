"""Contract tests for :mod:`reddit.data.preparation`.

The headline contract is that the label mapping comes from the configuration,
never from the contents of the gold file: deriving it from the data made the
training ids and the inference decoding two independent sources of truth.
"""

# `datasets` ships no type stubs; the resulting Unknowns are confined to this file.
# pyright: reportMissingTypeStubs=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from datasets import ClassLabel
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pandas import DataFrame

from reddit.core.config import Config, LabelsConfig
from reddit.core.errors import ConfigError, UndeclaredLabelError
from reddit.data.preparation import DataBundle, load_and_prepare_data

ROWS = 60


class TestPreparationModule:
    """Gold-dataset loading, cleaning, stratified splitting and label casting."""

    @pytest.fixture
    def write_gold(self, tmp_path: Path) -> Callable[..., Path]:
        """Factory: write a balanced gold Excel file into ``tmp_path``."""

        def _write(
            rows: int = ROWS,
            labels: list[str] | None = None,
            *,
            blank_text: int = 0,
            blank_label: int = 0,
            name: str = "gold.xlsx",
            extra_column: bool = True,
        ) -> Path:
            names = labels or ["down", "neutral", "up"]
            frame = DataFrame(
                {
                    "title": [f"headline number {i}" for i in range(rows)]
                    + [None] * blank_text
                    + [f"orphan {i}" for i in range(blank_label)],
                    "Label": [names[i % len(names)] for i in range(rows)]
                    + [names[0]] * blank_text
                    + [None] * blank_label,
                }
            )
            if extra_column:
                frame["irrelevant"] = "noise"
            target = tmp_path / name
            frame.to_excel(target, index=False)
            return target

        return _write

    @pytest.fixture
    def prepare(self, labels_config: LabelsConfig) -> Callable[..., DataBundle]:
        """Factory: run the preparation with the configured label authority."""

        def _prepare(path: Path, *, seed: int = 11, labels: LabelsConfig | None = None) -> DataBundle:
            return load_and_prepare_data(
                data_file_path=path,
                text_column="title",
                label_column="Label",
                test_size=0.10,
                validation_size=0.19,
                random_seed=seed,
                labels=labels or labels_config,
            )

        return _prepare

    @pytest.mark.unit
    class TestUnits:
        def test_the_bundle_exposes_the_configured_label_mapping(
            self, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle], labels_config: LabelsConfig
        ) -> None:
            bundle = prepare(write_gold())

            assert bundle.num_labels == labels_config.num_labels
            assert bundle.id2label == labels_config.id2label
            assert bundle.label2id == labels_config.label2id

        def test_the_bundle_is_immutable(
            self, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle]
        ) -> None:
            bundle = prepare(write_gold())

            with pytest.raises((AttributeError, TypeError)):
                bundle.num_labels = 5  # pyright: ignore[reportAttributeAccessIssue]

        def test_the_three_splits_are_produced(
            self, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle]
        ) -> None:
            bundle = prepare(write_gold())

            assert set(bundle.dataset) == {"train", "validation", "test"}

        def test_every_row_lands_in_exactly_one_split(
            self, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle]
        ) -> None:
            bundle = prepare(write_gold())

            total = sum(len(split) for split in bundle.dataset.values())
            assert total == ROWS
            texts = [text for split in bundle.dataset.values() for text in split["text"]]
            assert len(set(texts)) == ROWS

        def test_the_split_sizes_follow_the_configured_proportions(
            self, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle]
        ) -> None:
            bundle = prepare(write_gold())

            assert len(bundle.dataset["test"]) == round(ROWS * 0.10)
            assert len(bundle.dataset["validation"]) == pytest.approx(ROWS * 0.19, abs=1)

        def test_the_columns_are_renamed_for_the_trainer(
            self, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle]
        ) -> None:
            bundle = prepare(write_gold())

            assert set(bundle.dataset["train"].column_names) == {"text", "label_str", "label"}
            assert "irrelevant" not in bundle.dataset["train"].column_names

        def test_the_label_column_becomes_a_class_label_feature(
            self, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle], labels_config: LabelsConfig
        ) -> None:
            bundle = prepare(write_gold())

            feature = bundle.dataset["train"].features["label"]
            assert isinstance(feature, ClassLabel)
            assert feature.names == labels_config.names

        def test_label_ids_agree_with_their_names(
            self, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle], labels_config: LabelsConfig
        ) -> None:
            bundle = prepare(write_gold())
            train = bundle.dataset["train"]

            for label_id, label_str in zip(train["label"], train["label_str"], strict=True):
                assert labels_config.label2id[label_str] == label_id

        def test_labels_are_matched_case_insensitively(
            self, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle]
        ) -> None:
            bundle = prepare(write_gold(labels=["DOWN", "Neutral", "uP"]))

            assert set(bundle.dataset["train"]["label_str"]) <= {"down", "neutral", "up"}

        def test_rows_without_text_are_dropped(
            self, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle]
        ) -> None:
            bundle = prepare(write_gold(blank_text=6))

            assert sum(len(split) for split in bundle.dataset.values()) == ROWS

        def test_rows_without_a_label_are_dropped(
            self, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle]
        ) -> None:
            bundle = prepare(write_gold(blank_label=6))

            assert sum(len(split) for split in bundle.dataset.values()) == ROWS

        def test_an_undeclared_label_is_rejected(
            self, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle]
        ) -> None:
            gold = write_gold(labels=["down", "neutral", "up", "sideways"])

            with pytest.raises(UndeclaredLabelError, match="sideways"):
                prepare(gold)

        def test_the_rejection_lists_the_declared_labels(
            self, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle]
        ) -> None:
            gold = write_gold(labels=["down", "neutral", "up", "sideways"])

            with pytest.raises(UndeclaredLabelError) as excinfo:
                prepare(gold)

            assert "Declared labels: down, neutral, up" in str(excinfo.value)

        def test_the_rejection_is_a_user_fixable_configuration_error(
            self, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle]
        ) -> None:
            gold = write_gold(labels=["down", "neutral", "up", "sideways"])

            with pytest.raises(ConfigError):
                prepare(gold)

        def test_the_ordering_authority_is_the_config_not_the_alphabet(
            self, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle]
        ) -> None:
            """A reversed declaration must produce reversed ids, not alphabetical ones."""
            reversed_labels = LabelsConfig(
                labels={"up": 0, "neutral": 1, "down": 2},
                encodings={"up": 1, "neutral": 0, "down": -1},
            )

            bundle = prepare(write_gold(), labels=reversed_labels)

            assert bundle.label2id == {"up": 0, "neutral": 1, "down": 2}
            assert bundle.dataset["train"].features["label"].names == ["up", "neutral", "down"]
            train = bundle.dataset["train"]
            for label_id, label_str in zip(train["label"], train["label_str"], strict=True):
                assert reversed_labels.label2id[label_str] == label_id

    @pytest.mark.integration
    class TestIntegration:
        def test_a_missing_gold_file_surfaces_to_the_caller(
            self, prepare: Callable[..., DataBundle], tmp_path: Path
        ) -> None:
            with pytest.raises(FileNotFoundError):
                prepare(tmp_path / "absent.xlsx")

        def test_preparation_writes_nothing_next_to_the_gold_file(
            self, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle], tmp_path: Path
        ) -> None:
            gold = write_gold()
            before = sorted(p.name for p in tmp_path.iterdir())

            prepare(gold)

            assert sorted(p.name for p in tmp_path.iterdir()) == before

        def test_the_shipped_gold_dataset_prepares_cleanly(
            self, project_config: Config
        ) -> None:
            """The real ``data/labelled.xlsx`` must satisfy the declared label set."""
            bundle = load_and_prepare_data(
                data_file_path=project_config.dataset.path,
                text_column=project_config.dataset.text_column,
                label_column=project_config.dataset.label_column,
                test_size=project_config.dataset.test_size,
                validation_size=project_config.dataset.validation_size,
                random_seed=project_config.training.seeds[0],
                labels=project_config.labels,
            )

            assert bundle.num_labels == 3
            assert set(bundle.dataset) == {"train", "validation", "test"}
            assert bundle.dataset["train"].features["label"].names == project_config.labels.names

    @pytest.mark.contracts
    class TestContracts:
        @settings(deadline=None, max_examples=10, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(seed=st.integers(min_value=0, max_value=2**16))
        def test_the_same_seed_always_produces_the_same_split(
            self, seed: int, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle]
        ) -> None:
            gold = write_gold()

            first = prepare(gold, seed=seed)
            second = prepare(gold, seed=seed)

            assert first.dataset["train"]["text"] == second.dataset["train"]["text"]
            assert first.dataset["test"]["text"] == second.dataset["test"]["text"]

        @settings(deadline=None, max_examples=10, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(seed=st.integers(min_value=0, max_value=2**16))
        def test_no_row_is_ever_shared_between_splits(
            self, seed: int, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle]
        ) -> None:
            bundle = prepare(write_gold(), seed=seed)

            train = set(bundle.dataset["train"]["text"])
            validation = set(bundle.dataset["validation"]["text"])
            test = set(bundle.dataset["test"]["text"])

            assert train.isdisjoint(validation)
            assert train.isdisjoint(test)
            assert validation.isdisjoint(test)

        @settings(deadline=None, max_examples=10, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(seed=st.integers(min_value=0, max_value=2**16))
        def test_every_split_keeps_the_class_balance(
            self, seed: int, write_gold: Callable[..., Path], prepare: Callable[..., DataBundle]
        ) -> None:
            """The splits are stratified on the label."""
            bundle = prepare(write_gold(), seed=seed)

            for split in bundle.dataset.values():
                counts = [split["label"].count(label_id) for label_id in range(3)]
                assert max(counts) - min(counts) <= 1
