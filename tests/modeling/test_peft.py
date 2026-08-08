"""Contract tests for :mod:`reddit.modeling.peft`.

The registry ``peft_config`` is what ``training.llms.run_family`` validates a
family's declared methods against, so its key set is a cross-module contract.
"""

from __future__ import annotations

import typing

import pytest
import torch

from reddit.core.config import FineTuningMethod
from reddit.modeling.peft import peft_config, quantization_config, target_modules

DECLARABLE_METHODS = set(typing.get_args(FineTuningMethod.__value__))


class TestPeftModule:
    """Quantization and PEFT adapter configurations."""

    @pytest.mark.unit
    class TestUnits:
        def test_weights_are_quantized_to_four_bit_nf4(self) -> None:
            assert quantization_config.load_in_4bit is True
            assert quantization_config.bnb_4bit_quant_type == "nf4"

        def test_double_quantization_is_enabled(self) -> None:
            assert quantization_config.bnb_4bit_use_double_quant is True

        def test_the_compute_dtype_is_half_precision(self) -> None:
            assert quantization_config.bnb_4bit_compute_dtype is torch.float16

        def test_only_the_attention_projections_are_adapted(self) -> None:
            assert target_modules == ["q_proj", "k_proj", "v_proj", "o_proj"]

        def test_exactly_two_methods_are_implemented(self) -> None:
            assert set(peft_config) == {"qdora", "xqdora"}

        @pytest.mark.parametrize("method", ["qdora", "xqdora"])
        def test_every_adapter_is_a_dora_sequence_classifier(self, method: str) -> None:
            adapter = peft_config[method]

            assert adapter.use_dora is True
            assert str(adapter.task_type) == "SEQ_CLS"
            # peft normalises the declared list into a set, so order is not part
            # of the contract — the adapted module set is.
            assert set(adapter.target_modules or ()) == set(target_modules)

        def test_qdora_uses_the_wide_rank(self) -> None:
            adapter = peft_config["qdora"]

            assert adapter.r == 32
            assert adapter.lora_alpha == 32
            assert adapter.lora_dropout == 0.1

        def test_xqdora_uses_the_narrow_rank(self) -> None:
            adapter = peft_config["xqdora"]

            assert adapter.r == 4
            assert adapter.lora_alpha == 32
            assert adapter.lora_dropout == 0.05

        def test_the_two_adapters_differ_only_in_rank_and_dropout(self) -> None:
            qdora, xqdora = peft_config["qdora"], peft_config["xqdora"]

            assert (qdora.r, qdora.lora_dropout) != (xqdora.r, xqdora.lora_dropout)
            assert qdora.lora_alpha == xqdora.lora_alpha
            assert qdora.use_dora == xqdora.use_dora

        def test_every_implemented_method_is_declarable_in_a_config(self) -> None:
            assert set(peft_config) <= DECLARABLE_METHODS

        def test_some_declarable_methods_have_no_implementation(self) -> None:
            """``adalora`` is declarable; ``run_family`` rejects it at runtime."""
            unimplemented = DECLARABLE_METHODS - set(peft_config) - {"-"}

            assert unimplemented == {"adalora"}

        def test_the_registry_is_shared_not_rebuilt(self) -> None:
            from reddit.modeling.peft import peft_config as reimported

            assert reimported is peft_config
