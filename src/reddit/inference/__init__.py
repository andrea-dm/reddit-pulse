"""Full-corpus labelling: batched inference, answer-file updates, model discovery.

Importing this package pulls in torch; the task layer imports it only after
:func:`reddit.core.environment.prepare_environment` has run.
"""

from reddit.inference.corpus import CorpusJob, predict_corpus, update_answers
from reddit.inference.discovery import iter_model_archives, iter_model_dirs

__all__ = [
    "CorpusJob",
    "iter_model_archives",
    "iter_model_dirs",
    "predict_corpus",
    "update_answers",
]
