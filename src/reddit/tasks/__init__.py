"""CLI task modules: each exposes a ``setup_<name>`` / ``execute_<name>`` pair."""

from reddit.tasks.predict import execute_predict, setup_predict
from reddit.tasks.run import execute_run, setup_run

__all__ = [
    "execute_predict",
    "execute_run",
    "setup_predict",
    "setup_run",
]
