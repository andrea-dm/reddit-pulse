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


def setup_run(parser: ArgumentParser) -> None:
    """Register run/train-specific CLI arguments on the provided subparser."""
    parser.add_argument(
        "-f",
        "--family",
        type=str,
        required=True,
        help="Model family from the config's `families:` section (e.g. gemma, gemma_27, bert, test).",
    )
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
    """Run the training pipeline for the selected family.

    Returns:
        The number of runs that produced a selected checkpoint.
    """
    models = config.family(args.family)
    with_inference = not getattr(args, "no_inference", False)

    # Import pipelines only now: the environment (CUDA_VISIBLE_DEVICES,
    # HF_HOME) must be prepared before torch is imported.
    if models.kind == "bert":
        from reddit.inference.bert import label_corpus
        from reddit.training.bert import run_family
    else:
        from reddit.inference.llms import label_corpus
        from reddit.training.llms import run_family

    return run_family(
        config,
        models,
        labeller=label_corpus if with_inference else None,
        limit=max(0, args.limit),
    )
