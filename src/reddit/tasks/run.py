"""CLI task: the full pipeline (train -> select median seed -> label corpus).

``reddit train --family gemma`` is the same pipeline without the corpus
labelling stage.

This module is the composition root for training: it is the only place that
knows about both the training pipelines and the inference labeller, which is
what keeps ``training`` free of any dependency on ``inference``.
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace

from reddit.core.config import Config
from reddit.tasks.selection import add_selection_arguments, resolve_selection


def setup_run(parser: ArgumentParser) -> None:
    """Register run/train-specific CLI arguments on the provided subparser.

    Args:
        parser: The ``run``/``train`` subparser to add ``-f/--family``,
            ``-m/--model``, ``--all-families``, ``-l/--limit`` and
            ``--no-inference`` to (mutated in-place).
    """
    add_selection_arguments(parser)
    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=0,
        help="Limit the number of seeds to run (0 = all configured seeds).",
    )
    parser.add_argument(
        "--no-inference",
        action="store_true",
        default=False,
        help="Stop after training + median-seed selection; skip corpus labelling.",
    )


def execute_run(args: Namespace, config: Config) -> int:
    """Run the training pipeline for every selected family.

    Args:
        args: Parsed CLI namespace (``family``, ``model``, ``all_families``,
            ``limit``, ``no_inference``).
        config: Project configuration.

    Returns:
        The total number of runs that produced a selected checkpoint, across
        every selected family.
    """
    with_inference = not getattr(args, "no_inference", False)

    produced = 0
    for models in resolve_selection(args, config):
        # Import pipelines only now: the environment (CUDA_VISIBLE_DEVICES,
        # HF_HOME) must be prepared before torch is imported.
        if models.kind == "bert":
            from reddit.inference.bert import label_corpus
            from reddit.training.bert import run_family
        else:
            from reddit.inference.llms import label_corpus
            from reddit.training.llms import run_family

        produced += run_family(
            config,
            models,
            labeller=label_corpus if with_inference else None,
            limit=max(0, args.limit),
        )
    return produced
