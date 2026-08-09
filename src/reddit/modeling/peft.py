"""Quantization and PEFT (QDoRA+ / xQDoRA+) configurations.

Implements the two parameter-efficient fine-tuning recipes used to adapt the
quantized decoder LLM (SLM) classifiers: **QDoRA+** and its rank-reduced
variant **xQDoRA+**. Both combine three independent techniques:

1. **4-bit quantization** (:data:`quantization_config`): the frozen base
   weights are loaded in NF4 (double-quantized) precision.
2. **DoRA** (Weight-Decomposed Low-Rank Adaptation, ``use_dora=True``):
   decomposes each adapted weight matrix ``W`` into a magnitude vector and a
   direction matrix; only the direction is reparameterized with a low-rank
   (LoRA) update, while the magnitude is learned as a separate vector. This
   is a strictly richer parameterization than plain LoRA.
3. **LoRA+** (asymmetric learning rates for the low-rank ``A``/``B``
   adapter matrices): configured separately, at the optimizer level, in
   :meth:`reddit.training.llms.LlmSeedStrategy.optimizers`
   (``loraplus_lr_ratio=5``, i.e. :math:`\\eta_B = 5 \\cdot \\eta_A`).

:data:`peft_config` registers the two recipes:

- ``"qdora"`` (QDoRA+): the baseline configuration, adapter rank ``r=32``.
- ``"xqdora"`` (xQDoRA+): the efficiency-oriented variant, adapter rank
  ``r=4`` — an 8x (:math:`2^3`) reduction in trainable adapter parameters
  relative to QDoRA+. xQDoRA+ is not a degraded fallback: it trades a small
  amount of adapter capacity for materially faster wall-clock training,
  especially on the larger SLMs in the family.

See Also:
    :meth:`reddit.training.llms.LlmSeedStrategy.build_model`: Applies
        :data:`peft_config` to a quantized base model.
    :meth:`reddit.training.llms.LlmSeedStrategy.optimizers`: Applies the
        LoRA+ asymmetric learning-rate ratio that completes the recipe.
"""

import torch
from peft import LoraConfig
from transformers import BitsAndBytesConfig

# Explanation: 4-bit NF4 quantization of the frozen base model, applied to
# every decoder LLM classifier before PEFT adapters are attached (see
# `reddit.modeling.loading.llm_model_args`).
quantization_config = BitsAndBytesConfig(
    load_in_4bit=True,  # enable 4-bit quantization
    bnb_4bit_quant_type="nf4",  # information-theoretically optimal for normal weights
    bnb_4bit_use_double_quant=True,  # quantize the quantized weights
    bnb_4bit_compute_dtype=torch.float16,
)

# Rationale: adapting only the self-attention projections (query/key/value/
# output) keeps the adapter footprint small while still reaching every layer
# of the transformer stack.
target_modules = ["q_proj", "k_proj", "v_proj", "o_proj"]

peft_config = {
    "qdora": LoraConfig(  # QDoRA+: baseline recipe, rank r=32.
        task_type="SEQ_CLS",
        target_modules=target_modules,
        r=32,
        lora_alpha=32,
        lora_dropout=0.1,
        use_dora=True,
    ),
    "xqdora": LoraConfig(  # xQDoRA+: rank r=4, an 8x (2^3) reduction vs QDoRA+.
        task_type="SEQ_CLS",
        target_modules=target_modules,
        r=4,
        lora_alpha=32,
        lora_dropout=0.05,
        use_dora=True,
    ),
}
"""Registered fine-tuning recipes, keyed by the ``finetuning_methods`` name
used in ``config.yml`` (:class:`reddit.core.config.FineTuningMethod`).

Notes:
    ``"adalora"`` is a legal value of :data:`reddit.core.config.FineTuningMethod`
    but has no entry here; selecting it raises
    :class:`reddit.core.errors.UnsupportedMethodError` (see
    :func:`reddit.training.llms.run_family`). Only ``"qdora"`` and
    ``"xqdora"`` are implemented, matching the paper's QDoRA+/xQDoRA+ pair.
"""
