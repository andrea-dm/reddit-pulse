"""Structural interfaces shared across layers.

Keeping these in ``core`` lets ``training`` and ``inference`` depend on the
same contracts without depending on each other: the task layer injects a
concrete :class:`Labeller` into the training pipelines instead of
``training`` importing ``inference`` directly.
"""

from __future__ import annotations

from typing import Any, Protocol

from reddit.core.config import Config
from reddit.core.logging import LogLevel


class LogFn(Protocol):
    """Human-oriented progress logger.

    Concretely a :func:`reddit.core.logging.print_log` partial with ``dump``,
    ``filename`` and ``pid`` already bound.
    """

    def __call__(self, message: str | None = None, *, level: LogLevel | int | None = 0) -> None: ...


class Labeller(Protocol):
    """Labels the full Reddit corpus with one trained checkpoint.

    Implemented by :func:`reddit.inference.llms.label_corpus` and
    :func:`reddit.inference.bert.label_corpus`.  The BERT implementation
    ignores ``family`` and ``finetuning_method`` (its answer files are updated
    in place and its columns carry no method suffix); both are part of the
    signature so that the two are interchangeable at the injection point.
    """

    def __call__(
        self,
        *,
        config: Config,
        family: str,
        model_name: str,
        model_path: str,
        model_conf: Any,
        tokenizer: Any,
        finetuning_method: str,
        log: LogFn,
    ) -> None: ...
