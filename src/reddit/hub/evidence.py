"""What a model card and its companion files are built from.

The per-seed metric dumps written by :func:`reddit.training.loop.run_seeds`
(``outputs/{family}_{date}/dist_{model}_{test,train}_metrics.jsonl``), the
extract of ``config.yml`` that governed the run, and the pickled
``TrainingArguments`` every checkpoint carries — each turned into the text
the repository publishes next to the weights.
"""

from __future__ import annotations

import csv
import json
import logging
from io import StringIO
from typing import TYPE_CHECKING, Any, Literal, cast

from pandas import read_csv
from yaml import safe_dump

from reddit.core.utils import read_jsonl
from reddit.hub.card import SeedMetrics

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from reddit.core.config import Config, ModelKind

# The metrics `reddit.modeling.metrics.compute_metrics` produces, plus the
# Trainer's loss; runtime/throughput keys in the dumps are not published.
METRIC_KEYS: tuple[str, ...] = (
    "accuracy",
    "f1_weighted",
    "f1_macro",
    "precision_macro",
    "recall_macro",
    "roc_auc",
    "loss",
)
# Pickled `TrainingArguments` fields naming this box's directories.
LOCAL_PATH_FIELDS: tuple[str, ...] = ("output_dir", "logging_dir", "run_name")
TRAINING_ARGS_FILE = "training_args.bin"


def _latest_by_seed(paths: Iterable[Path], method: str) -> dict[int, dict[str, float]]:
    """``{seed: metrics}`` for one method across dumps; a repeated seed keeps its latest record."""
    by_seed: dict[int, dict[str, float]] = {}
    for path in sorted(paths):  # `{family}_{date}` directories sort chronologically
        for record in read_jsonl(path):
            seed = record.get("seed")
            if record.get("finetuning_method", "-") != method or not isinstance(seed, int):
                continue
            by_seed[seed] = {
                key: float(value) for key in METRIC_KEYS if isinstance(value := record.get(key), (int, float))
            }
    return by_seed


def seed_metrics(config: Config, model_name: str, method: str) -> tuple[SeedMetrics, ...]:
    """Every seed's validation and test metrics for one ``(model, method)`` run.

    Args:
        config: Project configuration (``paths.output_dir`` holds the dumps).
        model_name: Short model name the dumps are named after.
        method: ``"qdora"``/``"xqdora"``, or ``"-"`` for BERT.

    Returns:
        One entry per seed that has a test record, sorted by seed; the
        validation metrics of a seed without a train-dump record are empty.

    Notes:
        Reads every ``dist_{model}_*_metrics.jsonl`` under ``output_dir``
        (I/O), whatever run date they carry.
    """
    output_dir = config.paths.output_dir
    test = _latest_by_seed(output_dir.glob(f"*/dist_{model_name}_test_metrics.jsonl"), method)
    validation = _latest_by_seed(output_dir.glob(f"*/dist_{model_name}_train_metrics.jsonl"), method)
    return tuple(SeedMetrics(seed=seed, test=test[seed], validation=validation.get(seed, {})) for seed in sorted(test))


def evaluation_csv(seeds: Iterable[SeedMetrics], split: Literal["test", "validation"], selected: int) -> str:
    """The per-seed metrics table of one split as CSV text (sorted by weighted F1, best first).

    Args:
        seeds: The run's per-seed metrics.
        split: Which split's metrics to tabulate.
        selected: Seed of the published checkpoint, flagged in a
            ``selected`` column.

    Returns:
        CSV text with a header row; seeds lacking the split are skipped.
    """
    rows = [(s.seed, s.test if split == "test" else s.validation) for s in seeds]
    rows = [(seed, metrics) for seed, metrics in rows if metrics]
    rows.sort(key=lambda row: row[1].get("f1_weighted", float("-inf")), reverse=True)
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["seed", *METRIC_KEYS, "selected"])
    for seed, metrics in rows:
        writer.writerow([seed, *(metrics.get(key, "") for key in METRIC_KEYS), seed == selected])
    return buffer.getvalue()


def corpus_label_shares(config: Config, model_name: str, method: str) -> dict[str, dict[str, float]]:
    """Share of each label among the corpus rows the checkpoint labelled, per pass.

    Read from the standalone labelled CSVs ``reddit run``/``predict`` write
    under ``labels_dir`` (see :func:`reddit.inference.llms.build_jobs` and
    :func:`reddit.inference.bert.build_jobs` for the names); a pass whose
    CSV is absent is left out, so a checkpoint that was only trained yields
    an empty mapping and the card skips the section.

    Args:
        config: Project configuration (``paths.labels_dir``).
        model_name: Short model name.
        method: ``"qdora"``/``"xqdora"``, or ``"-"`` for BERT (no suffix).

    Returns:
        ``{"submissions": {label: share}, "comments": {...}}`` for the
        passes found, shares summing to 1 within a pass.

    Notes:
        Reads one column of each labelled CSV (I/O); a malformed file is
        logged and skipped.
    """
    suffix = "" if method == "-" else f"_{method}"
    column = f"{model_name}{suffix}_label"
    shares: dict[str, dict[str, float]] = {}
    for corpus_pass in ("submissions", "comments"):
        path = config.paths.labels_dir / f"{model_name}_{corpus_pass}{suffix}_predicted_labels.csv"
        if not path.is_file():
            continue
        try:
            counts = read_csv(path, usecols=[column])[column].value_counts(normalize=True)
        except (OSError, ValueError, KeyError) as e:
            logging.warning("Could not read the labels of `%s`: %s", path, e)
            continue
        shares[corpus_pass] = {str(label): float(share) for label, share in counts.items()}
    return shares


def training_config_extract(config: Config, kind: ModelKind, base_model: str, method: str, seed: int) -> str:
    """The part of ``config.yml`` that governed the checkpoint, as YAML text.

    Args:
        config: Project configuration.
        kind: ``"llm"`` or ``"bert"`` — selects which hyperparameter block
            applies.
        base_model: Hub id of the base checkpoint, recorded alongside.
        method: Fine-tuning method label, recorded alongside.
        seed: The published split seed, recorded alongside.

    Returns:
        YAML text with a short header comment, mirroring the extract shipped
        with the hand-written cards.
    """
    training = config.training
    if kind == "bert":
        hyperparameters: dict[str, Any] = {"bert": training.bert.model_dump()}
    else:
        hyperparameters = {
            "learning_rate": training.learning_rate,
            "num_train_epochs": training.num_train_epochs,
            "gradient_accumulation_steps": training.gradient_accumulation_steps,
            "gradient_checkpointing": training.gradient_checkpointing,
        }
    extract: dict[str, Any] = {
        "dataset": config.dataset.model_dump(exclude={"path"}),
        "labels": config.labels.model_dump(),
        "training": {
            "early_stopping_patience": training.early_stopping_patience,
            **hyperparameters,
            "arguments": training.arguments.model_dump(),
            "seeds": list(training.seeds),
        },
        "base_model": base_model,
        "finetuning_method": method,
        "selected_seed": seed,
    }
    header = (
        "# Extract of the project's config.yml that governs this checkpoint.\n"
        "# `training.seeds` vary the stratified split only; `training.arguments.seed`\n"
        "# fixes model initialisation and the Trainer's shuffling/dropout.\n"
    )
    return header + safe_dump(extract, sort_keys=False)


def training_args_json(checkpoint: Path) -> str | None:
    """The checkpoint's ``TrainingArguments`` as JSON text, local paths removed.

    Args:
        checkpoint: The checkpoint directory; its ``training_args.bin`` is
            the pickle ``Trainer.save_model`` wrote.

    Returns:
        Pretty-printed JSON, or ``None`` when the checkpoint carries no
        ``training_args.bin`` or the file cannot be unpickled (logged as a
        warning: the repository is still worth publishing without it).

    Notes:
        Unpickles ``training_args.bin`` with ``torch.load`` (I/O). The file
        is this project's own artefact, written by the training stage.
    """
    path = checkpoint / TRAINING_ARGS_FILE
    if not path.is_file():
        return None
    import torch  # noqa: PLC0415 — keeps the hub package importable without torch

    try:
        arguments: Any = torch.load(path, weights_only=False)
    except Exception as e:  # noqa: BLE001 — a corrupt pickle costs one companion file, not the upload
        logging.warning("Could not unpickle `%s`; publishing without `training_args.json`: %s", path, e)
        return None
    payload = dict(cast("dict[str, Any]", arguments.to_dict() if hasattr(arguments, "to_dict") else vars(arguments)))
    for field in LOCAL_PATH_FIELDS:
        if field in payload:
            payload[field] = None
    return json.dumps(payload, indent=2, default=str) + "\n"
