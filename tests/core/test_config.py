"""Contract tests for :mod:`reddit.core.config`.

Supersedes the original ``tests/test_config.py`` smoke suite; every assertion
made there is preserved (the two bootstrap-related cases moved to
``tests/core/test_environment.py``, which is where ``bootstrap_directories``
lives).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from reddit.core.config import (
    ArgumentsConfig,
    Config,
    DatasetConfig,
    EnvironmentConfig,
    Family,
    InferenceConfig,
    LabelsConfig,
    Models,
    ModelSpec,
    PathsConfig,
    SystemConfig,
    TrainingConfig,
    load_config,
)
from reddit.core.errors import ConfigError, UnknownFamilyError

LABEL_NAMES = st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=8)
PATH_FIELDS = tuple(PathsConfig.model_fields)


def paths_config(base: Path | str, **overrides: Path | str) -> PathsConfig:
    """Build a :class:`PathsConfig` with every directory under ``base``."""
    values: dict[str, Path | str] = {key: Path(base) / key for key in PATH_FIELDS}
    values.update(overrides)
    return PathsConfig(
        reddit_dir=Path(values["reddit_dir"]),
        cache_dir=Path(values["cache_dir"]),
        dumps_dir=Path(values["dumps_dir"]),
        output_dir=Path(values["output_dir"]),
        results_dir=Path(values["results_dir"]),
        models_dir=Path(values["models_dir"]),
        labels_dir=Path(values["labels_dir"]),
        logs_dir=Path(values["logs_dir"]),
    )


class TestConfigModule:
    """The unified configuration schema, its validators and family resolution."""

    @pytest.fixture
    def label_ids(self) -> dict[str, int]:
        """A valid, contiguous, zero-based label mapping."""
        return {"down": 0, "neutral": 1, "up": 2}

    @pytest.mark.unit
    class TestUnits:
        # ── loading the shipped configuration (preserved smoke assertions) ──

        def test_shipped_config_loads_with_the_declared_label_authority(self, project_config: Config) -> None:
            assert project_config.labels.num_labels == 3
            assert project_config.labels.id2label[2] == "up"
            assert project_config.labels.names == ["down", "neutral", "up"]
            assert project_config.training.seeds
            assert project_config.paths.reddit_dir.is_dir()  # ships with the repo

        def test_shipped_config_exposes_typed_sections(self, project_config: Config) -> None:
            assert project_config.inference.batch_size == 64
            assert project_config.inference.submissions is True
            # BERT hyperparameters, previously hardcoded in training/bert.py
            assert project_config.training.bert.learning_rate == 5e-5
            assert project_config.training.bert.num_train_epochs == 15

        def test_every_section_is_frozen(self, project_config: Config) -> None:
            with pytest.raises(ValidationError):
                project_config.training.seeds = []  # pyright: ignore[reportAttributeAccessIssue]

        @pytest.mark.parametrize(
            ("section", "field", "value"),
            [
                ("labels", "labels", {}),
                ("paths", "cache_dir", Path("/tmp")),
                ("inference", "batch_size", 1),
                ("system", "sleep_time", 99),
                ("dataset", "text_column", "other"),
                ("environment", "pytorch_alloc_conf", "x"),
            ],
        )
        def test_no_subsection_can_be_mutated(
            self, project_config: Config, section: str, field: str, value: object
        ) -> None:
            with pytest.raises(ValidationError):
                setattr(getattr(project_config, section), field, value)

        def test_shipped_config_declares_every_documented_family(self, project_config: Config) -> None:
            for name in ["bert", "gemma", "gemma_27", "llama", "qwen", "test"]:
                models = project_config.family(name)
                assert models.models, name
                assert all(m.name and m.id for m in models.models), name

            assert project_config.family("bert").kind == "bert"

            gemma = project_config.family("gemma_27")
            assert gemma.kind == "llm"
            assert gemma.finetuning_methods == ["qdora", "xqdora"]

            assert project_config.family("medium_part2").finetuning_methods == ["xqdora"]

        # ─────────────────────────────────────── label authority validation ──

        def test_label_properties_derive_from_the_declared_ids(self, label_ids: dict[str, int]) -> None:
            labels = LabelsConfig(labels=label_ids, encodings={"down": -1, "neutral": 0, "up": 1})

            assert labels.num_labels == 3
            assert labels.label2id == label_ids
            assert labels.id2label == {0: "down", 1: "neutral", 2: "up"}
            assert labels.names == ["down", "neutral", "up"]

        def test_label_order_follows_declared_ids_not_alphabet(self) -> None:
            """The config, not alphabetical accident, is the single ordering authority."""
            labels = LabelsConfig(
                labels={"up": 0, "neutral": 1, "down": 2},
                encodings={"up": 1, "neutral": 0, "down": -1},
            )

            assert labels.names == ["up", "neutral", "down"]
            assert labels.id2label[0] == "up"

        def test_label2id_is_a_copy_and_cannot_corrupt_the_config(self, label_ids: dict[str, int]) -> None:
            labels = LabelsConfig(labels=label_ids, encodings={"down": -1, "neutral": 0, "up": 1})

            labels.label2id["injected"] = 99

            assert "injected" not in labels.labels

        @pytest.mark.parametrize(
            "ids",
            [
                pytest.param({"down": 1, "neutral": 2, "up": 3}, id="does-not-start-at-zero"),
                pytest.param({"down": 0, "neutral": 1, "up": 3}, id="has-a-gap"),
                pytest.param({"down": 0, "neutral": 0, "up": 1}, id="duplicate-id"),
                pytest.param({"down": -1, "neutral": 0, "up": 1}, id="negative-id"),
            ],
        )
        def test_non_contiguous_label_ids_are_rejected(self, ids: dict[str, int]) -> None:
            with pytest.raises(ValidationError, match="contiguous"):
                LabelsConfig(labels=ids, encodings=dict.fromkeys(ids, 0))

        def test_encodings_must_declare_exactly_the_same_names(self, label_ids: dict[str, int]) -> None:
            with pytest.raises(ValidationError, match="exactly the same names"):
                LabelsConfig(labels=label_ids, encodings={"down": -1, "neutral": 0})

        def test_encodings_reject_extra_names(self, label_ids: dict[str, int]) -> None:
            with pytest.raises(ValidationError, match="exactly the same names"):
                LabelsConfig(
                    labels=label_ids,
                    encodings={"down": -1, "neutral": 0, "up": 1, "sideways": 2},
                )

        # ────────────────────────────────────────────── dataset validation ──

        def test_missing_dataset_file_raises_os_error(self, tmp_path: Path) -> None:
            """The existence check raises ``OSError``, which pydantic does not wrap."""
            with pytest.raises(OSError, match="does not exist"):
                DatasetConfig(path=tmp_path / "absent.xlsx", text_column="t", label_column="l")

        def test_dataset_path_must_not_be_a_directory(self, tmp_path: Path) -> None:
            with pytest.raises(ValidationError, match="not a valid input file"):
                DatasetConfig(path=tmp_path, text_column="t", label_column="l")

        def test_dataset_path_is_resolved_to_an_absolute_path(self, dataset_file: Path) -> None:
            dataset = DatasetConfig(path=dataset_file, text_column="t", label_column="l")

            assert dataset.path.is_absolute()
            assert dataset.path == dataset_file.resolve()

        def test_dataset_split_sizes_default_to_the_documented_values(self, dataset_file: Path) -> None:
            dataset = DatasetConfig(path=dataset_file, text_column="t", label_column="l")

            assert dataset.test_size == 0.10
            assert dataset.validation_size == 0.19

        # ──────────────────────────────────────────────── paths validation ──

        def test_paths_are_resolved_but_never_created(self, tmp_path: Path) -> None:
            paths = paths_config(tmp_path)

            assert all(getattr(paths, key).is_absolute() for key in PATH_FIELDS)
            assert not any(getattr(paths, key).exists() for key in PATH_FIELDS)

        def test_a_path_pointing_at_an_existing_file_is_rejected(self, tmp_path: Path) -> None:
            regular_file = tmp_path / "not_a_dir"
            regular_file.touch()

            with pytest.raises(ValidationError, match="must be a directory"):
                paths_config(tmp_path, cache_dir=regular_file)

        def test_paths_expand_environment_variables_and_user_home(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.setenv("REDDIT_TEST_ROOT", str(tmp_path))
            monkeypatch.setenv("HOME", str(tmp_path / "home"))

            paths = paths_config("$REDDIT_TEST_ROOT/shared", logs_dir=Path("~/logs"))

            assert paths.cache_dir == tmp_path / "shared" / "cache_dir"
            assert paths.logs_dir == tmp_path / "home" / "logs"

        # ───────────────────────────────────────── relative-path anchoring ──

        def test_relative_paths_anchor_to_the_config_directory(
            self, config_factory: Callable[..., Config], tmp_path: Path
        ) -> None:
            nested = tmp_path / "deeply" / "nested"

            config = config_factory(directory=nested)

            assert config.paths.cache_dir == nested / "cache"
            assert config.paths.logs_dir == nested / "logs"

        def test_absolute_paths_are_left_where_they_point(
            self, raw_config: dict[str, Any], write_config: Callable[..., Path], tmp_path: Path
        ) -> None:
            elsewhere = tmp_path / "elsewhere"
            raw_config["paths"]["cache_dir"] = str(elsewhere)

            config = load_config(write_config(raw_config, directory=tmp_path / "cfg"))

            assert config.paths.cache_dir == elsewhere

        def test_relocating_the_config_relocates_every_relative_path(
            self, raw_config: dict[str, Any], write_config: Callable[..., Path], tmp_path: Path
        ) -> None:
            """Metamorphic: the project is relocatable across the AML mounts."""
            here = load_config(write_config(deepcopy(raw_config), directory=tmp_path / "a"))
            there = load_config(write_config(deepcopy(raw_config), directory=tmp_path / "b"))

            for key in PATH_FIELDS:
                assert getattr(here.paths, key).relative_to(tmp_path / "a") == getattr(there.paths, key).relative_to(
                    tmp_path / "b"
                )

        def test_loading_the_same_file_twice_yields_equal_configurations(
            self, config_factory: Callable[..., Config]
        ) -> None:
            assert config_factory() == config_factory()

        # ──────────────────────────────────────────────── family resolution ──

        def test_family_resolves_to_the_pipeline_view(self, config: Config) -> None:
            models = config.family("llm_family")

            assert isinstance(models, Models)
            assert models.family == "llm_family"
            assert models.kind == "llm"
            assert [m.name for m in models.models] == ["tiny_llm"]

        def test_family_inherits_the_top_level_finetuning_methods(self, config: Config) -> None:
            assert config.families["llm_family"].finetuning_methods is None
            assert config.family("llm_family").finetuning_methods == ["qdora", "xqdora"]

        def test_family_declaration_overrides_the_top_level_default(self, config: Config) -> None:
            assert config.family("single_method").finetuning_methods == ["xqdora"]

        def test_bert_family_reports_its_kind(self, config: Config) -> None:
            assert config.family("bert_family").kind == "bert"

        def test_resolved_family_lists_are_detached_from_the_config(self, config: Config) -> None:
            models = config.family("llm_family")

            models.models.append(ModelSpec(name="smuggled", id="acme/smuggled"))
            models.finetuning_methods.append("adalora")

            assert [m.name for m in config.families["llm_family"].models] == ["tiny_llm"]
            assert config.training.finetuning_methods == ["qdora", "xqdora"]

        def test_resolved_family_is_frozen(self, config: Config) -> None:
            models = config.family("llm_family")

            with pytest.raises(ValidationError):
                models.family = "other"  # pyright: ignore[reportAttributeAccessIssue]

        def test_unknown_family_raises_unknown_family_error(self, config: Config) -> None:
            with pytest.raises(UnknownFamilyError):
                config.family("nope")

        def test_unknown_family_is_still_a_key_error(self, config: Config) -> None:
            """Backward compatibility for `except KeyError` call sites."""
            with pytest.raises(KeyError):
                config.family("nope")

        def test_unknown_family_message_lists_the_available_families(self, config: Config) -> None:
            with pytest.raises(UnknownFamilyError) as excinfo:
                config.family("nope")

            message = str(excinfo.value)
            assert "nope" in message
            assert "bert_family, llm_family, single_method" in message

        def test_families_default_to_empty(self, config_factory: Callable[..., Config]) -> None:
            config = config_factory(families={})

            assert config.families == {}
            with pytest.raises(UnknownFamilyError):
                config.family("anything")

        # ──────────────────────────────────────── hf_home precedence rules ──

        def test_hf_home_prefers_an_exported_environment_variable(
            self, config_factory: Callable[..., Config], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.setenv("HF_HOME", str(tmp_path / "exported"))
            config = config_factory(environment={"hf_home": str(tmp_path / "configured")})

            assert config.hf_home == tmp_path / "exported"

        def test_hf_home_falls_back_to_the_configured_value(
            self, config_factory: Callable[..., Config], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.delenv("HF_HOME", raising=False)
            config = config_factory(environment={"hf_home": str(tmp_path / "configured")})

            assert config.hf_home == tmp_path / "configured"

        def test_hf_home_falls_back_to_the_platform_default(
            self, config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.delenv("HF_HOME", raising=False)
            monkeypatch.setenv("HOME", str(tmp_path / "home"))

            assert config.hf_home == Path(tmp_path / "home") / ".cache" / "huggingface"

        def test_environment_paths_are_expanded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
            monkeypatch.setenv("REDDIT_TEST_ROOT", str(tmp_path))

            env = EnvironmentConfig(hf_home=Path("$REDDIT_TEST_ROOT/hf"), dotenv=None)

            assert env.hf_home == tmp_path / "hf"
            assert env.dotenv is None

        # ─────────────────────────────────────────────── defaults & literals ──

        def test_section_defaults_match_the_documented_values(self) -> None:
            assert InferenceConfig().batch_size == 64
            assert InferenceConfig().submissions is True
            assert InferenceConfig().comments is True
            assert SystemConfig().sleep_time == 0
            assert EnvironmentConfig().pytorch_alloc_conf == "expandable_segments:True"
            assert TrainingConfig(seeds=[1]).finetuning_methods == ["qdora", "xqdora"]
            assert TrainingConfig(seeds=[1]).early_stopping_patience == 5
            assert TrainingConfig(seeds=[1]).bert.learning_rate == 5e-5
            assert TrainingConfig(seeds=[1]).learning_rate == 1e-4
            assert Family(models=[]).kind == "llm"

        def test_system_date_defaults_to_a_yyyymmdd_stamp(self) -> None:
            date = SystemConfig().date

            assert len(date) == 8
            assert date.isdigit()

        def test_training_arguments_defaults_reproduce_the_legacy_constants(self) -> None:
            arguments = ArgumentsConfig()

            assert arguments.metric_for_best_model == "f1_weighted"
            assert arguments.greater_is_better is True
            assert arguments.load_best_model_at_end is True
            assert arguments.eval_strategy == "epoch"
            assert arguments.save_total_limit == 2

        def test_an_unsupported_performance_metric_is_rejected(self) -> None:
            with pytest.raises(ValidationError):
                ArgumentsConfig(metric_for_best_model="auc")  # pyright: ignore[reportArgumentType]

        def test_an_unsupported_evaluation_strategy_is_rejected(self) -> None:
            with pytest.raises(ValidationError):
                ArgumentsConfig(eval_strategy="batch")  # pyright: ignore[reportArgumentType]

        def test_unknown_finetuning_method_is_rejected(self) -> None:
            with pytest.raises(ValidationError):
                Family(models=[], finetuning_methods=["magic"])  # pyright: ignore[reportArgumentType]

        def test_unknown_model_kind_is_rejected(self) -> None:
            with pytest.raises(ValidationError):
                Family(models=[], kind="mamba")  # pyright: ignore[reportArgumentType]

        @pytest.mark.parametrize("section", ["dataset", "paths", "labels", "training"])
        def test_mandatory_sections_cannot_be_omitted(
            self, raw_config: dict[str, Any], write_config: Callable[..., Path], section: str
        ) -> None:
            raw_config.pop(section)
            config_path = write_config(raw_config)

            with pytest.raises(ConfigError):
                load_config(config_path)

        def test_load_config_expands_the_path_it_is_given(
            self, config_factory: Callable[..., Config], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            config_factory()  # writes tmp_path/config.yml
            monkeypatch.setenv("REDDIT_TEST_ROOT", str(tmp_path))

            config = load_config("$REDDIT_TEST_ROOT/config.yml")

            assert config.paths.cache_dir == tmp_path / "cache"

        def test_missing_config_file_raises_config_error(self, tmp_path: Path) -> None:
            """Unreadable files are wrapped like every other config problem."""
            with pytest.raises(ConfigError):
                load_config(tmp_path / "absent.yml")

        def test_loading_a_config_exports_nothing_to_the_environment(
            self, config_factory: Callable[..., Config]
        ) -> None:
            before = os.environ.copy()

            config_factory()

            assert os.environ == before

    @pytest.mark.integration
    class TestIntegration:
        def test_loading_creates_nothing_on_disk(self, config_factory: Callable[..., Config], tmp_path: Path) -> None:
            """Validation is a pure read; directories appear only via the bootstrap."""
            config = config_factory()

            assert config.paths.cache_dir == tmp_path / "cache"
            assert not any(getattr(config.paths, key).exists() for key in PATH_FIELDS)

        def test_loading_the_shipped_config_creates_nothing_new(
            self,
            project_config_raw: dict[str, Any],
            write_config: Callable[..., Path],
            tmp_path: Path,
            repo_root: Path,
        ) -> None:
            """The original smoke test, relocated: a scratch cache dir stays absent."""
            scratch = tmp_path / "scratch"
            # Relative paths anchor to the config's own directory, so the dataset
            # has to be pinned absolutely once the config moves to a temp dir.
            project_config_raw["dataset"]["path"] = str((repo_root / "data" / "labelled.xlsx").resolve())
            project_config_raw["paths"]["cache_dir"] = str(scratch)

            config = load_config(write_config(project_config_raw))

            assert config.paths.cache_dir == scratch
            assert not scratch.exists()

        def test_paths_reached_through_a_symlink_resolve_to_their_target(
            self, raw_config: dict[str, Any], write_config: Callable[..., Path], tmp_path: Path
        ) -> None:
            """``PathsConfig`` calls ``Path.resolve()``, so symlinks are followed."""
            real = tmp_path / "real"
            real.mkdir()
            link = tmp_path / "link"
            link.symlink_to(real, target_is_directory=True)
            raw_config["paths"]["cache_dir"] = str(link / "cache")

            config = load_config(write_config(raw_config))

            assert config.paths.cache_dir == real / "cache"

    @pytest.mark.contracts
    class TestContracts:
        @given(
            names=st.lists(LABEL_NAMES, min_size=1, max_size=6, unique=True),
            rotation=st.integers(min_value=0, max_value=5),
        )
        def test_any_contiguous_assignment_is_accepted_and_ordered_by_id(self, names: list[str], rotation: int) -> None:
            ids = [(i + rotation) % len(names) for i in range(len(names))]
            mapping = dict(zip(names, ids, strict=True))

            labels = LabelsConfig(labels=mapping, encodings=dict.fromkeys(names, 0))

            assert labels.num_labels == len(names)
            assert labels.names == [labels.id2label[i] for i in range(len(names))]
            assert all(labels.label2id[name] == mapping[name] for name in names)
            assert sorted(labels.id2label) == list(range(len(names)))

        @given(
            names=st.lists(LABEL_NAMES, min_size=1, max_size=5, unique=True),
            shift=st.integers(min_value=1, max_value=20),
        )
        def test_shifting_every_id_breaks_contiguity(self, names: list[str], shift: int) -> None:
            mapping = {name: i + shift for i, name in enumerate(names)}

            with pytest.raises(ValidationError, match="contiguous"):
                LabelsConfig(labels=mapping, encodings=dict.fromkeys(names, 0))

        @given(
            names=st.lists(LABEL_NAMES, min_size=2, max_size=5, unique=True),
            encodings=st.lists(st.integers(min_value=-9, max_value=9), min_size=2, max_size=5),
        )
        def test_encoding_values_are_unconstrained_only_their_names_matter(
            self, names: list[str], encodings: list[int]
        ) -> None:
            mapping = {name: i for i, name in enumerate(names)}
            values = (encodings * len(names))[: len(names)]

            labels = LabelsConfig(labels=mapping, encodings=dict(zip(names, values, strict=True)))

            assert set(labels.encodings) == set(labels.labels)
            assert labels.num_labels == len(names)

        @settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(
            segments=st.lists(
                st.text(alphabet="abcdefghij", min_size=1, max_size=6),
                min_size=1,
                max_size=3,
            )
        )
        def test_any_relative_path_anchors_below_the_config_directory(
            self,
            segments: list[str],
            raw_config: dict[str, Any],
            write_config: Callable[..., Path],
            tmp_path: Path,
        ) -> None:
            relative = "/".join(segments)
            raw = deepcopy(raw_config)
            raw["paths"]["cache_dir"] = relative

            config = load_config(write_config(raw, name="anchored.yml"))

            assert config.paths.cache_dir == (tmp_path / relative).resolve()
            assert config.paths.cache_dir.is_relative_to(tmp_path)

        @settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(seeds=st.lists(st.integers(min_value=0, max_value=2**32 - 1), min_size=1, max_size=8))
        def test_seed_lists_survive_a_load_round_trip(
            self, seeds: list[int], raw_config: dict[str, Any], write_config: Callable[..., Path]
        ) -> None:
            raw = deepcopy(raw_config)
            raw["training"]["seeds"] = seeds

            config = load_config(write_config(raw, name="seeded.yml"))

            assert config.training.seeds == seeds

        @given(name=st.text(min_size=1, max_size=12).filter(lambda s: s not in {"llm", "bert"}))
        def test_every_unknown_family_name_raises_a_config_error(self, name: str) -> None:
            config = Config(
                dataset=DatasetConfig(path=Path(__file__), text_column="t", label_column="l"),
                paths=paths_config(Path(__file__).parent),
                labels=LabelsConfig(labels={"a": 0}, encodings={"a": 0}),
                training=TrainingConfig(seeds=[1]),
                families={
                    "llm": Family(models=[ModelSpec(name="m", id="a/m")]),
                    "bert": Family(models=[ModelSpec(name="m", id="a/m")], kind="bert"),
                },
            )

            with pytest.raises(ConfigError):
                config.family(name)
