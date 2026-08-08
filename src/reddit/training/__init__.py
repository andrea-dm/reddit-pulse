"""Training pipelines: multi-seed fine-tuning and median-seed selection.

Importing this package pulls in torch; the task layer imports it only after
:func:`reddit.core.environment.prepare_environment` has run.
"""

from reddit.training.loop import SeedContext, SeedResult, SeedStrategy, run_seeds
from reddit.training.selection import select_median

__all__ = [
    "SeedContext",
    "SeedResult",
    "SeedStrategy",
    "run_seeds",
    "select_median",
]
