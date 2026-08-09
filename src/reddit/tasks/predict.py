"""CLI task: inference-only labelling from saved checkpoints."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace

from reddit.core.config import Config


def setup_predict(parser: ArgumentParser) -> None:
    """Register predict-specific CLI arguments on the provided subparser.

    Args:
        parser: The ``predict`` subparser to add ``-f/--family`` and
            ``-d/--directory`` to (mutated in-place).
    """
    parser.add_argument(
        "-f",
        "--family",
        type=str,
        required=True,
        help="Model family from the config's `families:` section; only checkpoints of its models are used.",
    )
    parser.add_argument(
        "-d",
        "--directory",
        type=str,
        required=True,
        help=(
            "Directory to scan for checkpoints: `{model}_{method}_{seed}.zip` archives "
            "for LLM families, `{model}_{seed}` folders for BERT families."
        ),
    )


def execute_predict(args: Namespace, config: Config) -> int:
    """Label the corpus with every matching checkpoint in the directory.

    Args:
        args: Parsed CLI namespace (``family``, ``directory``).
        config: Project configuration.

    Returns:
        The number of checkpoints that were labelled.
    """
    models = config.family(args.family)

    # Deferred import: environment must be prepared before torch loads.
    if models.kind == "bert":
        from reddit.inference.bert import predict_from_directories as predict_fn
    else:
        from reddit.inference.llms import predict_from_archives as predict_fn

    return predict_fn(config, models, args.directory)
