"""Domain exceptions.

The pipelines previously signalled every failure with either a bare
``Exception`` catch or a raw stdlib error (``KeyError`` for an unknown family,
a pandas ``FileNotFoundError`` for a missing answers file).  These types give
the CLI something specific to catch and turn into a readable message.
"""

from __future__ import annotations


class RedditError(Exception):
    """Base class for every error this package raises deliberately."""


class ConfigError(RedditError):
    """The project configuration is unusable or internally inconsistent.

    The CLI turns these into ``parser.error`` messages rather than tracebacks.
    """


class UnknownFamilyError(ConfigError, KeyError):
    """``--family`` names a family that the config does not declare.

    Also a :class:`KeyError` so that existing ``except KeyError`` call sites
    (and the config smoke tests) keep working.
    """

    def __str__(self) -> str:  # KeyError.__str__ would add quotes around the message
        return self.args[0] if self.args else ""


class UnknownModelError(ConfigError, KeyError):
    """``--model`` names a model that no declared family contains.

    Also a :class:`KeyError`, for symmetry with :class:`UnknownFamilyError`.
    """

    def __str__(self) -> str:  # KeyError.__str__ would add quotes around the message
        return self.args[0] if self.args else ""


class UnsupportedMethodError(ConfigError):
    """A declared fine-tuning method has no implementation registered."""


class UndeclaredLabelError(ConfigError):
    """The gold dataset contains a label the config does not declare."""


class CorpusUnavailableError(RedditError):
    """A corpus or answers file required for labelling is missing."""
