"""Command-line entry point for the Reddit inflation-direction labelling pipeline.

Wires the pipeline subcommands under a single ``main()`` dispatcher.  Adding
a new pipeline requires only:

1. Implementing a ``setup_<name>`` / ``execute_<name>`` pair in a task module.
2. Registering a new subparser block here.

See Also:
    `Advanced — Operations: Workflows <advanced/operations/workflows.md>`_:
        Full call-chain walkthrough of every subcommand below.

Examples:
    Typical invocations from the project root::

        reddit run --family gemma --gpu 0
        reddit run --family gemma bert --gpu 0
        reddit run --model gemma2_2b bert_base --gpu 0
        reddit run --all-families --gpu 0
        reddit run --family test --limit 1 --gpu 0
        reddit train --model gemma2_27b --gpu 0
        reddit predict --family bert --directory models
        python -m reddit --help
"""

from __future__ import annotations

import logging
import sys
from argparse import ArgumentParser, Namespace, RawTextHelpFormatter
from pathlib import Path
from time import sleep

from reddit import __version__
from reddit.core.config import Config, load_config
from reddit.core.environment import bootstrap_directories, prepare_environment
from reddit.core.errors import ConfigError, RedditError
from reddit.core.logging import setup_logging
from reddit.tasks.predict import execute_predict, setup_predict
from reddit.tasks.run import execute_run, setup_run
from reddit.tasks.selection import selection_log_name

DEFAULT_CONFIG = "config.yml"


def _add_common_arguments(parser: ArgumentParser) -> None:
    """Register the ``-c/--config`` and ``-g/--gpu`` flags shared by every subcommand."""
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default=None,
        help=f"Path to the unified configuration YAML (default: ./{DEFAULT_CONFIG}).",
    )
    parser.add_argument(
        "-g",
        "--gpu",
        type=str,
        default=None,
        help='Value for CUDA_VISIBLE_DEVICES (e.g. "0" or "0,1"); unset by default.',
    )


def _resolve_config_path(args: Namespace) -> Path:
    """Resolve the config path: ``--config``, else ``./config.yml``, else the repo-root default."""
    if args.config:
        return Path(args.config)
    candidate = Path.cwd() / DEFAULT_CONFIG
    if candidate.exists():
        return candidate
    # fall back to the config shipped next to the installed project (repo root)
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / DEFAULT_CONFIG


def build_parser() -> ArgumentParser:
    """Construct the top-level argument parser with all subcommands."""
    parser = ArgumentParser(
        prog="reddit",
        description=__doc__,
        formatter_class=RawTextHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Train over all seeds, select the median seed, then label the corpus.",
    )
    setup_run(run_parser)
    _add_common_arguments(run_parser)
    run_parser.set_defaults(func=execute_run)

    train_parser = subparsers.add_parser(
        "train",
        help="Train over all seeds and select the median seed (no corpus labelling).",
    )
    setup_run(train_parser)
    _add_common_arguments(train_parser)
    train_parser.set_defaults(func=execute_run, no_inference=True)

    predict_parser = subparsers.add_parser(
        "predict",
        help="Label the corpus with previously trained checkpoints.",
    )
    setup_predict(predict_parser)
    _add_common_arguments(predict_parser)
    predict_parser.set_defaults(func=execute_predict)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, bootstrap the environment and dispatch the subcommand."""
    parser = build_parser()
    args = parser.parse_args(argv)

    config_path = _resolve_config_path(args)
    if not config_path.exists():
        parser.error(f"Config file not found: {config_path}")
    try:
        config: Config = load_config(config_path)
    except ConfigError as e:
        parser.error(str(e))

    # Loading a config no longer creates anything; do it explicitly here.
    bootstrap_directories(config)

    # Logging first: `prepare_environment` warns about a missing Hugging Face
    # token, and that warning must reach the log file rather than an unhandled
    # root logger.
    selection = selection_log_name(args)
    log_name = f"{args.command}_{selection}.log" if selection else f"{args.command}.log"
    setup_logging(log_file=config.paths.logs_dir / log_name)
    logging.info("Loaded config from `%s`.", config_path)

    # Env vars (CUDA_VISIBLE_DEVICES, HF_HOME, allocator) must be exported
    # before the task imports torch.
    prepare_environment(config, gpu=args.gpu)

    if (sleep_time := config.system.sleep_time) > 0:
        logging.info("Sleeping for %s seconds...", f"{sleep_time:,d}")
        sleep(sleep_time)
        logging.info("Sleeping for %s seconds... done.", f"{sleep_time:,d}")

    try:
        produced = args.func(args, config)
    except ConfigError as e:
        # Unknown family, unsupported method, undeclared label: user-fixable
        # configuration problems deserve a message, not a traceback.
        parser.error(str(e))
    except RedditError:
        logging.exception("`%s` failed", args.command)
        return 1

    if not produced:
        logging.error("`%s` finished without producing any model or labelled checkpoint.", args.command)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
