"""Configuration schema and loaders.

A single ``config.yml`` at the project root replaces the previous
``config.yaml`` + one-yaml-per-model-family (``config_bert.yaml``,
``config_gemma.yaml``, ...) layout.  Model families now live under the
``families:`` key and are selected at the CLI with ``--family``.

Relative paths in the config are resolved against the directory containing
the config file, so the project stays relocatable across the AML mounts
(``/home/azureuser/cloudfiles/...`` and ``/mnt/batch/...`` point at the same
share).

Every section is a typed, frozen model: the pipelines index nothing by string
and cannot mutate the configuration they were handed.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator, model_validator
from yaml import YAMLError, safe_load

from reddit.core.errors import ConfigError, UnknownFamilyError

type FineTuningMethod = Literal["qdora", "xqdora", "adalora", "-"]
type PerformanceMetric = Literal["f1_weighted", "f1", "accuracy", "recall", "precision"]
type ModelKind = Literal["llm", "bert"]

_FROZEN = ConfigDict(frozen=True)


def _expand(raw: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(raw))))


class ModelSpec(BaseModel):
    """A single model entry inside a family."""

    model_config = _FROZEN

    name: str
    id: str


class Family(BaseModel):
    """A named group of models trained/predicted together.

    ``finetuning_methods`` is optional; when omitted the top-level
    ``training.finetuning_methods`` default applies (see :meth:`Config.family`).
    """

    model_config = _FROZEN

    models: list[ModelSpec]
    finetuning_methods: list[FineTuningMethod] | None = None
    kind: ModelKind = "llm"


class Models(BaseModel):
    """A resolved family selection, as consumed by the pipelines.

    Produced only by :meth:`Config.family`, which is why ``finetuning_methods``
    is *not* optional here: resolution against the top-level default has
    already happened, so no consumer needs to repeat it.
    """

    model_config = _FROZEN

    family: str
    models: list[ModelSpec]
    finetuning_methods: list[FineTuningMethod]
    kind: ModelKind = "llm"


class EnvironmentConfig(BaseModel):
    """Process-environment knobs previously exported by the ``launch_*.sh`` scripts."""

    model_config = _FROZEN

    hf_home: Path | None = None
    dotenv: Path | None = None
    pytorch_alloc_conf: str | None = "expandable_segments:True"

    @field_validator("hf_home", "dotenv", mode="before")
    @classmethod
    def _expand_path(cls, v: Any) -> Any:
        return _expand(v) if v is not None else None


class SystemConfig(BaseModel):
    model_config = _FROZEN

    date: str = Field(default_factory=lambda: datetime.now().strftime("%Y%m%d"))
    sleep_time: int = 0


class DatasetConfig(BaseModel):
    model_config = _FROZEN

    path: Path
    text_column: str
    label_column: str
    test_size: float = 0.10
    validation_size: float = 0.19

    @field_validator("path")
    @classmethod
    def check_if_exists(cls, v: Any) -> Path:
        p = _expand(v)
        if not p.exists():
            raise OSError(f"The file `{p}` does not exist.")
        if p.is_dir():
            raise ValueError(f"`{p}` is not a valid input file")
        return p.resolve()


class LabelsConfig(BaseModel):
    """The single authority for the label set.

    Training ids and inference decoding both read from here; deriving the
    mapping from the gold file made them two independent sources of truth that
    agreed only because alphabetical order happened to match.
    """

    model_config = _FROZEN

    labels: dict[str, int]
    encodings: dict[str, int]

    @model_validator(mode="after")
    def _check_consistent(self) -> LabelsConfig:
        ids = sorted(self.labels.values())
        if ids != list(range(len(ids))):
            raise ValueError(f"`labels.labels` ids must be contiguous and start at 0, got {ids}")
        if set(self.encodings) != set(self.labels):
            raise ValueError("`labels.encodings` must declare exactly the same names as `labels.labels`")
        return self

    @property
    def num_labels(self) -> int:
        return len(self.labels)

    @property
    def id2label(self) -> dict[int, str]:
        return {v: k for k, v in self.labels.items()}

    @property
    def label2id(self) -> dict[str, int]:
        return dict(self.labels)

    @property
    def names(self) -> list[str]:
        """Label names ordered by id — the ``datasets.ClassLabel`` ordering."""
        id2label = self.id2label
        return [id2label[i] for i in range(len(id2label))]


class PathsConfig(BaseModel):
    """Project directories.

    Validation is a pure read: the directories are created by
    :func:`reddit.core.environment.bootstrap_directories`, not here.
    """

    model_config = _FROZEN

    reddit_dir: Path
    cache_dir: Path
    dumps_dir: Path
    output_dir: Path
    results_dir: Path
    models_dir: Path
    labels_dir: Path
    logs_dir: Path

    @field_validator("*", mode="before")
    @classmethod
    def _expand_value(cls, v: Any) -> Any:
        return _expand(v) if isinstance(v, (str, Path)) else v

    @field_validator("*", mode="after")
    @classmethod
    def _check_is_dir(cls, p: Path, info: ValidationInfo) -> Path:
        if p.exists() and not p.is_dir():
            raise ValueError(f"the path `paths['{info.field_name}']` must be a directory. Found: `{p}`")
        return p.resolve()


class InferenceConfig(BaseModel):
    """Corpus-labelling knobs."""

    model_config = _FROZEN

    batch_size: int = 64
    submissions: bool = True
    comments: bool = True


class ArgumentsConfig(BaseModel):
    """Pass-through subset of ``transformers.TrainingArguments``.

    Every field here must be an accepted ``TrainingArguments.__init__``
    parameter for the installed transformers version: the strategies splat
    ``model_dump()`` straight into the constructor, and an unknown key raises
    ``TypeError`` inside the per-seed loop, silently failing every seed.
    (``overwrite_output_dir`` was dropped when transformers 5 removed it.)
    """

    model_config = _FROZEN

    save_total_limit: int = 2
    save_on_each_node: bool = False
    report_to: str | None = None
    push_to_hub: bool = False
    disable_tqdm: bool = True
    # --- Performance ---
    bf16: bool = False
    fp16: bool = True
    per_device_train_batch_size: int = 64
    per_device_eval_batch_size: int = 64
    weight_decay: float = 0.01
    dataloader_num_workers: int = 4
    dataloader_pin_memory: bool = True
    # --- Strategy ---
    eval_strategy: Literal["epoch", "step"] = "epoch"
    save_strategy: Literal["epoch", "step"] = "epoch"
    load_best_model_at_end: bool = True
    metric_for_best_model: PerformanceMetric = "f1_weighted"
    greater_is_better: bool = True


class BertTrainingConfig(BaseModel):
    """Full fine-tuning hyperparameters for BERT-family encoders.

    Previously hardcoded as module constants in ``training/bert.py``, which
    made ``training.learning_rate`` silently inapplicable to that pipeline.
    The defaults below reproduce those constants exactly.
    """

    model_config = _FROZEN

    learning_rate: float = 5e-5
    num_train_epochs: int = 15


class TrainingConfig(BaseModel):
    model_config = _FROZEN

    early_stopping_patience: int = 5
    arguments: ArgumentsConfig = Field(default_factory=ArgumentsConfig)
    seeds: list[int]
    # Default methods for families that do not declare their own.
    finetuning_methods: list[FineTuningMethod] = Field(default_factory=lambda: ["qdora", "xqdora"])
    learning_rate: float = 1e-4
    num_train_epochs: int = 40
    gradient_accumulation_steps: int = 8
    bert: BertTrainingConfig = Field(default_factory=BertTrainingConfig)


class Config(BaseModel):
    model_config = _FROZEN

    system: SystemConfig = Field(default_factory=SystemConfig)
    dataset: DatasetConfig
    paths: PathsConfig
    labels: LabelsConfig
    training: TrainingConfig
    inference: InferenceConfig = Field(default_factory=InferenceConfig)
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    families: dict[str, Family] = Field(default_factory=dict)

    @property
    def hf_home(self) -> Path:
        """Effective Hugging Face home directory.

        Mirrors the precedence :func:`reddit.core.environment.prepare_environment`
        establishes with ``setdefault``: an explicitly exported ``HF_HOME`` wins,
        then the configured value, then the platform default.  Reading it here
        rather than via ``os.environ["HF_HOME"]`` keeps the pipelines usable
        outside the CLI bootstrap.
        """
        if env := os.environ.get("HF_HOME"):
            return Path(env)
        if self.environment.hf_home:
            return self.environment.hf_home
        return Path.home() / ".cache" / "huggingface"

    def family(self, name: str) -> Models:
        """Resolve a family by name into the :class:`Models` view used by pipelines."""
        if name not in self.families:
            available = ", ".join(sorted(self.families))
            raise UnknownFamilyError(f"Unknown model family `{name}`. Available: {available}")
        fam = self.families[name]
        return Models(
            family=name,
            models=list(fam.models),
            finetuning_methods=list(fam.finetuning_methods or self.training.finetuning_methods),
            kind=fam.kind,
        )


def _resolve_relative_paths(raw: dict[str, Any], base: Path) -> dict[str, Any]:
    """Anchor every relative path in the raw mapping to ``base``."""

    def anchor(value: str | Path) -> str:
        p = _expand(value)
        return str(p if p.is_absolute() else base / p)

    paths = raw.get("paths")
    if isinstance(paths, dict):
        # Rationale: yaml parsing yields untyped containers; the shape is
        # validated by pydantic right after this function runs.
        typed_paths = cast("dict[str, str | Path]", paths)
        raw["paths"] = {k: anchor(v) for k, v in typed_paths.items()}
    dataset = raw.get("dataset")
    if isinstance(dataset, dict) and "path" in dataset:
        typed_dataset = cast("dict[str, str | Path]", dataset)
        typed_dataset["path"] = anchor(typed_dataset["path"])
    return raw


def load_config(path_to_config: str | Path) -> Config:
    """Load and validate the unified project configuration.

    Raises:
        ConfigError: the file cannot be read, is not valid YAML, or fails
            schema validation.  Pydantic's ``ValidationError`` and validator
            ``OSError``\\ s are wrapped so callers (the CLI in particular) can
            turn any user-fixable configuration problem into a usage message
            with a single ``except ConfigError``.
    """
    path = _expand(path_to_config).resolve()
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = safe_load(f)
        return Config.model_validate(_resolve_relative_paths(raw, path.parent))
    except (OSError, YAMLError, ValidationError) as e:
        raise ConfigError(f"Invalid configuration `{path}`: {e}") from e
