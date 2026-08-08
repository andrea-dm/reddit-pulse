"""Reddit sentiment labelling pipeline.

Fine-tunes decoder LLMs (QDoRA/xQDoRA) and BERT-family encoders for
inflation-trend sequence classification over a hand-labelled gold dataset,
selects the median-performing seed, and labels the full Reddit corpus
(submissions and comments from r/economy, r/Economics, r/wallstreetbets).
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__: str = version("reddit")
except PackageNotFoundError:  # editable/uninstalled fallback
    __version__ = "0.0.0+unknown"

del PackageNotFoundError, version
