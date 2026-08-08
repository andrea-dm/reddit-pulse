"""Core building blocks: configuration schema, errors, logging and shared utilities."""

from reddit.core.config import Config, Models, ModelSpec, load_config
from reddit.core.environment import bootstrap_directories, prepare_environment
from reddit.core.errors import (
    ConfigError,
    CorpusUnavailableError,
    RedditError,
    UndeclaredLabelError,
    UnknownFamilyError,
    UnsupportedMethodError,
)
from reddit.core.logging import print_log, setup_logging
from reddit.core.protocols import Labeller, LogFn

__all__ = [
    "Config",
    "ConfigError",
    "CorpusUnavailableError",
    "Labeller",
    "LogFn",
    "ModelSpec",
    "Models",
    "RedditError",
    "UndeclaredLabelError",
    "UnknownFamilyError",
    "UnsupportedMethodError",
    "bootstrap_directories",
    "load_config",
    "prepare_environment",
    "print_log",
    "setup_logging",
]
