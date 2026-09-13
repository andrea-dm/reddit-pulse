"""Publishing selected checkpoints to the Hugging Face Hub.

Three pieces, all fed by the ``upload`` task (the composition root that
also knows discovery and the PEFT registry): :mod:`reddit.hub.card` renders
the model card, :mod:`reddit.hub.evidence` collects the per-seed metrics and
configuration extracts the card and its companion files are built from, and
:mod:`reddit.hub.upload` stages the repository folder and pushes it.
"""

from reddit.hub.card import CheckpointCard, LabelRow, SeedMetrics, render_model_card
from reddit.hub.evidence import evaluation_csv, seed_metrics, training_args_json, training_config_extract
from reddit.hub.upload import base_model_license, publish, stage_checkpoint

__all__ = [
    "CheckpointCard",
    "LabelRow",
    "SeedMetrics",
    "base_model_license",
    "evaluation_csv",
    "publish",
    "render_model_card",
    "seed_metrics",
    "stage_checkpoint",
    "training_args_json",
    "training_config_extract",
]
