"""Contract tests for :mod:`reddit.modeling.loading`.

This module is the single owner of *how* a checkpoint is materialised; the
argument dictionaries it returns are its entire public contract.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import torch

from reddit.modeling.loading import BERT_MAX_LENGTH, LLM_MAX_LENGTH, bert_model_args, llm_model_args
from reddit.modeling.peft import quantization_config


class TestLoadingModule:
    """Truncation lengths and model-loading policy."""

    @pytest.mark.unit
    class TestUnits:
        def test_truncation_lengths_match_the_two_architectures(self) -> None:
            assert LLM_MAX_LENGTH == 1024
            assert BERT_MAX_LENGTH == 512

        def test_decoder_llms_are_loaded_quantized_with_flash_attention(self) -> None:
            args = llm_model_args()

            assert args["dtype"] is torch.bfloat16
            assert args["quantization_config"] is quantization_config
            assert args["attn_implementation"] == "flash_attention_2"
            assert args["low_cpu_mem_usage"] is True
            assert args["device_map"] == "auto"

        def test_the_llm_policy_declares_nothing_else(self) -> None:
            assert set(llm_model_args()) == {
                "dtype",
                "quantization_config",
                "attn_implementation",
                "low_cpu_mem_usage",
                "device_map",
            }

        def test_encoders_are_loaded_in_bfloat16_without_quantization(self) -> None:
            assert bert_model_args() == {"dtype": torch.bfloat16}

        def test_encoders_are_never_dispatched_by_accelerate(self) -> None:
            """``device_map`` would make ``_prepare_model_device`` skip the ``.to()``."""
            assert "device_map" not in bert_model_args()
            assert "quantization_config" not in bert_model_args()

        @pytest.mark.parametrize("factory", [llm_model_args, bert_model_args])
        def test_each_call_returns_an_independent_mapping(self, factory: Callable[[], dict[str, Any]]) -> None:
            first = factory()
            first["dtype"] = "corrupted"

            assert factory()["dtype"] is torch.bfloat16

        def test_the_two_policies_never_share_a_dictionary(self) -> None:
            assert llm_model_args() is not bert_model_args()
