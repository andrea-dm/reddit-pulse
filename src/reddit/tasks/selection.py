"""Shared ``--family``/``--model``/``--all-families`` CLI selection.

Both the ``run``/``train`` and ``predict`` subcommands let the user choose
which models to operate on the same three mutually exclusive ways, so the
argparse wiring and its resolution against :class:`~reddit.core.config.Config`
live here once instead of being duplicated per task module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import ArgumentParser, Namespace

    from reddit.core.config import Config, Models


def add_selection_arguments(parser: ArgumentParser) -> None:
    """Register the mutually exclusive model-selection flags on ``parser``.

    Args:
        parser: The ``run``/``train``/``predict`` subparser to add ``-f/--family``,
            ``-m/--model`` and ``--all-families``/``--all-models`` to (mutated in-place).
    """
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "-f",
        "--family",
        nargs="+",
        metavar="FAMILY",
        default=None,
        help="One or more model families from the config's `families:` section (e.g. gemma bert).",
    )
    group.add_argument(
        "-m",
        "--model",
        nargs="+",
        metavar="MODEL",
        default=None,
        help="One or more model names, looked up across every declared family.",
    )
    group.add_argument(
        "--all-families",
        "--all-models",
        action="store_true",
        dest="all_families",
        default=False,
        help="Run every model in every declared family.",
    )


def resolve_selection(args: Namespace, config: Config) -> list[Models]:
    """Resolve the parsed ``--family``/``--model``/``--all-families`` selection.

    Args:
        args: Parsed CLI namespace (``family``, ``model``, ``all_families``).
        config: Project configuration.

    Returns:
        One :class:`Models` per matched family.
    """
    if args.all_families:
        return config.resolve_families(None)
    if args.model:
        return config.resolve_models(args.model)
    return config.resolve_families(args.family)


def selection_log_name(args: Namespace) -> str | None:
    """Build the log-file suffix describing the parsed selection, if any."""
    if getattr(args, "all_families", False):
        return "all"
    if family := getattr(args, "family", None):
        return "_".join(family)
    if model := getattr(args, "model", None):
        return "_".join(model)
    return None
