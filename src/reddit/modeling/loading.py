"""Model-loading policy: dtype, quantization, attention implementation.

Single owner of *how* a checkpoint is materialized, for both training and
inference.  Previously this decision was spread across
``inference.llms.build_model_args``, inline ``{"dtype": torch.bfloat16}``
literals in the two training pipelines, and the ``fp16``/``bf16`` flags in
``config.yml`` — with ``training`` importing ``inference`` to reach it.
"""

from __future__ import annotations

from typing import Any

import torch

from reddit.modeling.peft import quantization_config

# Truncation lengths, previously duplicated across the training and inference
# modules of each kind.
LLM_MAX_LENGTH = 1024
BERT_MAX_LENGTH = 512


def llm_model_args() -> dict[str, Any]:
    """4-bit + flash-attention load arguments for decoder LLM classifiers."""
    return {
        "dtype": torch.bfloat16,
        "quantization_config": quantization_config,
        "attn_implementation": "flash_attention_2",
        "low_cpu_mem_usage": True,
        "device_map": "auto",
    }


def bert_model_args() -> dict[str, Any]:
    """Load arguments for fully fine-tuned BERT-family encoders."""
    return {"dtype": torch.bfloat16}
