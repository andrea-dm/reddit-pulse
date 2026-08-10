"""Reddit inflation-direction labelling pipeline.

Fine-tunes decoder LLMs (QDoRA+/xQDoRA+ PEFT) and BERT-family encoders for
three-way directional inflation-expectation classification (UP/DOWN/NEUTRAL,
not generic sentiment) over a hand-labelled gold dataset, selects the
median-performing seed, and labels the full Reddit corpus (submissions and
comments from r/economy, r/Economics, r/wallstreetbets).

This package implements the fine-tuning and full-corpus inference stages of
the pipeline described in Del Monaco, Longo, Marcucci & Tafani, "Reddit's
'pulse' on US inflation: forecasting with large language models" (Banca
d'Italia Questioni di Economia e Finanza 1028, June 2026). It does not
implement the paper's corpus-filtering, seed-label-construction or
signal-aggregation stages — see
`Advanced — Implementation Design: System Overview <advanced/implementation_design/system_overview.md#scope-boundary>`_
for the full stage-by-stage scope boundary.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__: str = version("reddit")
except PackageNotFoundError:  # editable/uninstalled fallback
    __version__ = "0.0.0+unknown"

del PackageNotFoundError, version
