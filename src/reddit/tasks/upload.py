"""CLI task: publish selected checkpoints to the Hugging Face Hub.

``reddit upload --model qwen2.5_0.5b`` scans ``models/`` for that model's
archives (checkpoint directories for BERT families), assembles one
repository folder per checkpoint under ``outputs/hub/`` — adapter or full
weights, tokenizer, ``config.json``, a generated model card,
``training_args.json``, ``training_config.yml`` and the per-seed evaluation
tables — and pushes it to ``hub.namespace``. ``--dry-run`` stops after
staging so the card can be reviewed first.

This is the second composition root: like :mod:`reddit.tasks.run` it is
the only place that combines discovery (``inference``), the PEFT registry
(``modeling``), the gold-set reader (``data``) and the ``hub`` package.
"""

# transformers configs and tokenizers are untyped; the Unknowns stay in this file.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from reddit.core.errors import HubError
from reddit.hub.card import CheckpointCard, LabelRow, render_model_card
from reddit.hub.evidence import (
    corpus_label_shares,
    evaluation_csv,
    seed_metrics,
    training_args_json,
    training_config_extract,
)
from reddit.hub.upload import (
    IGNORED_CHECKPOINT_FILES,
    MODEL_CARD_FILE,
    add_to_collection,
    base_license_files,
    base_model_license,
    publish,
    stage_checkpoint,
)
from reddit.tasks.selection import add_selection_arguments, resolve_selection

if TYPE_CHECKING:
    from argparse import ArgumentParser, Namespace
    from collections.abc import Iterator

    from reddit.core.config import Config, ModelKind, Models, ModelSpec
    from reddit.hub.card import SeedMetrics

DEFAULT_DIRECTORY = "models"
STAGING_SUBDIR = "hub"
# The dotenv's write token; `HF_TOKEN` (the read token training downloads
# with) is the fallback so a single all-purpose token also works.
WRITE_TOKEN_VARIABLES: tuple[str, ...] = ("HF_WRITE_TOKEN", "HF_TOKEN")


def setup_upload(parser: ArgumentParser) -> None:
    """Register upload-specific CLI arguments on the provided subparser.

    Args:
        parser: The ``upload`` subparser to add ``-f/--family``, ``-m/--model``,
            ``--all-families``, ``-d/--directory``, ``--dry-run`` and
            ``--private``/``--public`` to (mutated in-place).
    """
    add_selection_arguments(parser)
    parser.add_argument(
        "-d",
        "--directory",
        type=str,
        default=DEFAULT_DIRECTORY,
        help=(
            "Directory to scan for selected checkpoints: `{model}_{method}_{seed}.zip` archives "
            f"for LLM families, `{{model}}_{{seed}}` folders for BERT families (default: {DEFAULT_DIRECTORY})."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Stage every repository folder under outputs/hub/ and report it, without touching the Hub.",
    )
    visibility = parser.add_mutually_exclusive_group()
    visibility.add_argument(
        "--private",
        dest="private",
        action="store_const",
        const=True,
        default=None,
        help="Create the repositories as private (overrides `hub.private`).",
    )
    visibility.add_argument(
        "--public",
        dest="private",
        action="store_const",
        const=False,
        help="Create the repositories as public (overrides `hub.private`).",
    )


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """One selected checkpoint found on disk.

    Attributes:
        model: Its model spec (short name + base Hub id).
        method: ``"qdora"``/``"xqdora"``, or ``"-"`` for BERT.
        seed: The split seed parsed from the checkpoint name.
        path: The (unzipped) checkpoint directory.
    """

    model: ModelSpec
    method: str
    seed: int
    path: Path


def _seed_of(path: Path) -> int:
    """The trailing ``_{seed}`` of a checkpoint name (discovery already validated it)."""
    return int(path.name.rsplit("_", 1)[1])


def discover(models: Models, directory: str | Path) -> Iterator[Checkpoint]:
    """Yield every checkpoint of the selected models found under ``directory``.

    LLM archives are unzipped one at a time by
    :func:`reddit.inference.discovery.iter_model_archives` and removed once
    the consumer advances, so each yielded checkpoint must be staged before
    the next one is requested.

    Args:
        models: The resolved family selection.
        directory: Directory holding the checkpoints.

    Yields:
        One :class:`Checkpoint` per matching archive or directory.
    """
    # Imported only now: torch must not load before the environment is prepared.
    from reddit.inference.discovery import iter_model_archives, iter_model_dirs  # noqa: PLC0415 — after env prep

    specs = {m.name: m for m in models.models}
    if models.kind == "bert":
        for name, path in iter_model_dirs(directory):
            if name in specs:
                yield Checkpoint(specs[name], "-", _seed_of(Path(path)), Path(path))
        return
    for name, method, path in iter_model_archives(directory, methods=models.finetuning_methods):
        if name in specs:
            yield Checkpoint(specs[name], method, _seed_of(Path(path)), Path(path))


def checkpoint_conflicts(models: Models, directory: str | Path) -> dict[tuple[str, str], list[str]]:
    """The selected ``(model, method)`` pairs that have more than one checkpoint under ``directory``.

    Every checkpoint of a pair publishes to the same repository, so a second
    one (an archive left behind by an earlier run: training itself keeps only
    the median seed) would overwrite the first. Only names are read; nothing
    is unzipped.

    Args:
        models: The resolved family selection.
        directory: Directory holding the checkpoints.

    Returns:
        ``{(model, method): checkpoint file or folder names}`` for every pair
        found more than once; ``method`` is ``"-"`` for BERT. Empty when
        there is no conflict or the directory does not exist.
    """
    from reddit.inference.discovery import parse_archive_name, parse_dir_name  # noqa: PLC0415 — after env prep

    parent = Path(directory)
    if not parent.is_dir():
        return {}
    names = {m.name for m in models.models}
    found: dict[tuple[str, str], list[str]] = {}
    if models.kind == "bert":
        for path in sorted(parent.iterdir()):
            if path.is_dir() and (parsed := parse_dir_name(path.name)) and parsed[0] in names:
                found.setdefault((parsed[0], "-"), []).append(path.name)
    else:
        methods = set(models.finetuning_methods)
        for path in sorted(parent.glob("*.zip")):
            if (parsed := parse_archive_name(path.stem, methods)) and parsed[0] in names:
                found.setdefault((parsed[0], parsed[1]), []).append(path.name)
    return {pair: checkpoints for pair, checkpoints in found.items() if len(checkpoints) > 1}


def _refuse_conflicts(selection: list[Models], directory: str | Path) -> None:
    """Raise before anything is staged when a repository would receive two checkpoints."""
    conflicts = {pair: names for models in selection for pair, names in checkpoint_conflicts(models, directory).items()}
    if not conflicts:
        return
    entries: list[str] = []
    for (model, method), names in sorted(conflicts.items()):
        label = model if method == "-" else f"{model} {method}"
        entries.append(f"{label}: {', '.join(names)}")
    raise HubError(
        f"More than one checkpoint would publish to the same repository ({'; '.join(entries)}). Training keeps "
        f"only the median seed, so the others are left over from earlier runs: move them out of `{directory}` "
        "and retry."
    )


def _hyperparameters(config: Config, kind: ModelKind, method: str) -> tuple[tuple[str, str], ...]:
    """The ``(setting, value)`` rows of the card's procedure table."""
    # Both imports pull in torch; deferred for the same reason as `discover`.
    from reddit.modeling.loading import BERT_MAX_LENGTH, LLM_MAX_LENGTH  # noqa: PLC0415 — after env prep
    from reddit.modeling.peft import peft_config  # noqa: PLC0415 — after env prep

    training = config.training
    arguments = training.arguments
    precision = "bf16 mixed precision" if arguments.bf16 else "fp16 mixed precision" if arguments.fp16 else "fp32"
    common: tuple[tuple[str, str], ...] = (
        ("Objective", "Cross-entropy with `balanced` class weights (`sklearn.utils.class_weight`)"),
        ("Batch size", f"{arguments.per_device_train_batch_size} (train and eval), dynamic padding to multiples of 8"),
        ("Checkpoint", "best epoch by validation weighted-F1 (`load_best_model_at_end`)"),
        ("Precision", precision),
        ("Weight decay", str(arguments.weight_decay)),
    )
    if kind == "bert":
        return (
            ("Optimizer", "AdamW (fused) over every parameter"),
            ("Learning rate", f"{training.bert.learning_rate:g}, linear decay, no warm-up"),
            *common,
            (
                "Epochs",
                (
                    f"up to {training.bert.num_train_epochs}, early stopping with patience "
                    f"{training.early_stopping_patience} on validation weighted-F1"
                ),
            ),
            ("Max sequence length", f"{BERT_MAX_LENGTH} tokens (truncation only)"),
        )
    lora = peft_config[method]
    targets = ", ".join(f"`{m}`" for m in sorted(lora.target_modules or ()))
    return (
        ("Base model", "4-bit NF4, double-quantized (`bitsandbytes`), frozen"),
        (
            "Adapters",
            (
                f"DoRA (`use_dora`), rank r = {lora.r}, alpha = {lora.lora_alpha}, dropout = {lora.lora_dropout}, "
                f"on {targets}; classification head trained in full"
            ),
        ),
        ("Optimizer", "AdamW (fused) with LoRA+ (adapter `B` matrices at 5x the base learning rate)"),
        ("Learning rate", f"{training.learning_rate:g}, cosine decay, no warm-up"),
        *common,
        (
            "Gradient accumulation",
            (
                f"{training.gradient_accumulation_steps} micro-batches per optimizer step "
                f"(effective batch {arguments.per_device_train_batch_size * training.gradient_accumulation_steps})"
            ),
        ),
        (
            "Epochs",
            (
                f"up to {training.num_train_epochs}, early stopping with patience "
                f"{training.early_stopping_patience} on validation weighted-F1"
            ),
        ),
        ("Gradient checkpointing", "on (reentrant)" if training.gradient_checkpointing else "off"),
        ("Max sequence length", f"{LLM_MAX_LENGTH} tokens (truncation only)"),
    )


def _ensure_model_config(found: Checkpoint, config: Config) -> None:
    """Write ``config.json`` into an adapter checkpoint saved before the training loop did.

    The card's usage snippet relies on the file (head size, label names, pad
    token); an older archive gets the same config the training stage would
    have saved, rebuilt from the base model and the checkpoint's tokenizer.

    Notes:
        Reads the base config from the Hub cache and writes ``config.json``
        into ``found.path`` (I/O); a no-op when the file already exists or
        the checkpoint is not a PEFT adapter.
    """
    from reddit.modeling.loading import adapter_base_model  # noqa: PLC0415 — after env prep

    if (found.path / "config.json").is_file() or adapter_base_model(found.path) is None:
        return
    from transformers import AutoConfig, AutoTokenizer  # noqa: PLC0415 — after env prep

    labels = config.labels
    model_config: Any = AutoConfig.from_pretrained(
        found.model.id,
        num_labels=labels.num_labels,
        id2label=labels.id2label,
        label2id=labels.label2id,
        use_cache=False,
    )
    tokenizer: Any = AutoTokenizer.from_pretrained(found.path)
    model_config.pad_token_id = tokenizer.pad_token_id
    model_config.save_pretrained(found.path)
    logging.warning("`%s` carried no `config.json`; rebuilt it from `%s`.", found.path.name, found.model.id)


def _label_rows(config: Config) -> tuple[LabelRow, ...]:
    labels = config.labels
    return tuple(LabelRow(id=i, name=name, encoding=labels.encodings[name]) for i, name in enumerate(labels.names))


def _gold_counts(config: Config) -> dict[str, int]:
    """Gold-set rows per label; empty (with a warning) when the file cannot be read."""
    from reddit.data.preparation import label_counts  # noqa: PLC0415 — after env prep

    dataset = config.dataset
    try:
        return label_counts(dataset.path, dataset.text_column, dataset.label_column)
    except Exception as e:  # noqa: BLE001 — a card without the data table is still worth publishing
        logging.warning("Could not read the gold dataset `%s` for the model card: %s", dataset.path, e)
        return {}


def write_token() -> str | None:
    """The Hub token uploads use: ``HF_WRITE_TOKEN`` first, then ``HF_TOKEN``."""
    return next((token for name in WRITE_TOKEN_VARIABLES if (token := os.environ.get(name))), None)


def _companions(
    config: Config, kind: ModelKind, found: Checkpoint, seeds: tuple[SeedMetrics, ...], token: str | None
) -> dict[str, str]:
    """The text files published next to the weights, ``{relative name: content}``."""
    companions = base_license_files(found.model.id, token=token)
    companions |= {
        "training_config.yml": training_config_extract(config, kind, found.model.id, found.method, found.seed),
        "evaluation/seeds_test_metrics.csv": evaluation_csv(seeds, "test", found.seed),
        "evaluation/seeds_validation_metrics.csv": evaluation_csv(seeds, "validation", found.seed),
    }
    if (arguments := training_args_json(found.path)) is not None:
        companions["training_args.json"] = arguments
    return companions


def _card(
    config: Config,
    models: Models,
    found: Checkpoint,
    *,
    seeds: tuple[SeedMetrics, ...],
    companions: dict[str, str],
    gold_counts: dict[str, int],
    token: str | None,
) -> CheckpointCard:
    """Collect every fact the model card prints for one checkpoint."""
    from reddit.modeling.loading import BERT_MAX_LENGTH, LLM_MAX_LENGTH  # noqa: PLC0415 — after env prep

    dataset = config.dataset
    # PEFT drops a stub README.md into the checkpoint; the generated card replaces it.
    skipped = IGNORED_CHECKPOINT_FILES | {MODEL_CARD_FILE}
    checkpoint_files = sorted(p.name for p in found.path.iterdir() if p.is_file() and p.name not in skipped)
    return CheckpointCard(
        repo_id=config.hub.repo_id(found.model.name, found.method),
        model_name=found.model.name,
        base_model=found.model.id,
        kind=models.kind,
        method=found.method,
        seed=found.seed,
        labels=_label_rows(config),
        seeds=seeds,
        hyperparameters=_hyperparameters(config, models.kind, found.method),
        gold_counts=gold_counts,
        split_shares=(1 - dataset.validation_size - dataset.test_size, dataset.validation_size, dataset.test_size),
        max_length=BERT_MAX_LENGTH if models.kind == "bert" else LLM_MAX_LENGTH,
        base_license=base_model_license(found.model.id, token=token),
        files=(*checkpoint_files, *sorted(companions), MODEL_CARD_FILE),
        license_files=tuple(
            name for name in sorted(companions) if name.upper().startswith(("LICEN", "NOTICE", "USE_POLICY"))
        ),
        corpus_shares=corpus_label_shares(config, found.model.name, found.method),
    )


def stage(config: Config, models: Models, found: Checkpoint, *, gold_counts: dict[str, int], token: str | None) -> Path:
    """Build the model card and stage one checkpoint's repository folder.

    Args:
        config: Project configuration.
        models: The family selection ``found`` belongs to (its ``kind``).
        found: The checkpoint to stage.
        gold_counts: Gold-set rows per label, for the card's data table.
        token: Hub token used to read the base model's license and files.

    Returns:
        The staged folder, ``outputs/hub/{repository name}``.

    Notes:
        Reads the metric dumps, the labelled corpus CSVs and the checkpoint
        (I/O), fetches the base model's license metadata and files
        (network), and writes the staging folder (I/O).
    """
    _ensure_model_config(found, config)
    seeds = seed_metrics(config, found.model.name, found.method)
    companions = _companions(config, models.kind, found, seeds, token)
    card = _card(config, models, found, seeds=seeds, companions=companions, gold_counts=gold_counts, token=token)
    staging = config.paths.output_dir / STAGING_SUBDIR / card.name
    files = stage_checkpoint(found.path, staging, card=render_model_card(card), companions=companions)
    logging.info("Staged `%s` for `%s`: %s", staging, card.repo_id, ", ".join(files))
    return staging


def execute_upload(args: Namespace, config: Config) -> int:
    """Stage (and, unless ``--dry-run``, publish) every selected checkpoint.

    Args:
        args: Parsed CLI namespace (``family``, ``model``, ``all_families``,
            ``directory``, ``dry_run``, ``private``).
        config: Project configuration (``hub`` section).

    Returns:
        The number of checkpoints staged or published.

    Raises:
        HubError: publishing was requested without a Hub token, a selected
            model and method has more than one checkpoint under
            ``directory`` (see :func:`checkpoint_conflicts`; raised before
            anything is staged, dry run included), or the Hub rejected an
            upload. A failure to add a published repository to
            ``hub.collection`` is logged, not raised: the upload itself
            succeeded.
        ConfigError: ``hub.namespace`` is not set.
    """
    token = write_token()
    if not args.dry_run and not token:
        raise HubError(
            "No Hugging Face token found: put a token with write access in the dotenv as HF_WRITE_TOKEN "
            "(HF_TOKEN is used as a fallback). Use --dry-run to stage without publishing."
        )
    selection = resolve_selection(args, config)
    _refuse_conflicts(selection, args.directory)
    private = config.hub.private if args.private is None else bool(args.private)
    gold_counts = _gold_counts(config)

    processed = 0
    for models in selection:
        for found in discover(models, args.directory):
            staging = stage(config, models, found, gold_counts=gold_counts, token=token)
            repo_id = config.hub.repo_id(found.model.name, found.method)
            if args.dry_run:
                logging.info("Dry run: `%s` staged at `%s`, nothing uploaded.", repo_id, staging)
            else:
                url = publish(staging, repo_id, private=private, token=token)
                logging.info("Published `%s` (%s) -> %s", repo_id, "private" if private else "public", url)
                _collect(repo_id, config.hub.collection, token)
            processed += 1
    return processed


def _collect(repo_id: str, collection: str | None, token: str | None) -> None:
    """Add a published repository to the configured collection; a failure is logged, not raised."""
    if not collection:
        return
    try:
        slug = add_to_collection(repo_id, collection, token=token)
    except HubError as e:
        logging.error("`%s` was published but not added to the collection: %s", repo_id, e)
        return
    logging.info("Added `%s` to collection `%s`.", repo_id, slug)
