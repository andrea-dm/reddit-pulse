"""Model-loading policy: dtype, quantization, attention implementation.

Single owner of *how* a checkpoint is materialized, for both training and
inference.  Previously this decision was spread across
``inference.llms.build_model_args``, inline ``{"dtype": torch.bfloat16}``
literals in the two training pipelines, and the ``fp16``/``bf16`` flags in
``config.yml`` — with ``training`` importing ``inference`` to reach it.

The policy is resolved against the *device* (:func:`detect_device_profile`)
rather than hardcoded: bfloat16 arithmetic and the flash-attention 2 kernels
are both Ampere-or-newer (``sm_80+``) features.  Requesting them
unconditionally made every LLM load raise ``ImportError`` on a Turing box
(Tesla T4, ``sm_75``) — where ``flash-attn`` cannot even be installed — and
ran bfloat16 through emulation, several times slower than fp16, wherever it
did load.
"""

from __future__ import annotations

import importlib.util
import json
from copy import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import torch

from reddit.modeling.peft import build_quantization_config

# Truncation lengths, previously duplicated across the training and inference
# modules of each kind.
LLM_MAX_LENGTH = 1024
BERT_MAX_LENGTH = 512

type AttentionImplementation = Literal["flash_attention_2", "sdpa"]

# What `peft.PeftModel.save_pretrained` writes next to the adapter weights;
# its presence is what makes a checkpoint directory an adapter rather than a
# full model.
ADAPTER_CONFIG_FILE = "adapter_config.json"


def strip_quantization(model_config: Any) -> Any:
    """A copy of a model config without the quantization it was trained under.

    transformers records ``quantization_config`` on the config of a model
    loaded in 4-bit, and saves it with ``config.json``. Fed back into
    ``from_pretrained`` next to an explicit ``quantization_config`` (see
    :func:`llm_model_args`), that stale block makes transformers treat the
    Hub's full-precision base weights as pre-quantized: the linear layers
    come back as plain parameters and PEFT then fails to attach the
    adapter (``'Parameter' object has no attribute 'compress_statistics'``).
    The checkpoint's config should describe the architecture, the head and
    the labels; how the base is quantized is decided at load time.

    Args:
        model_config: A ``PretrainedConfig`` (or any object carrying the
            attribute); left untouched.

    Returns:
        A shallow copy without ``quantization_config`` (and without the
        ``_pre_quantization_dtype`` marker that accompanies it).
    """
    stripped = copy(model_config)
    attributes: dict[str, Any] = getattr(stripped, "__dict__", {})
    for attribute in ("quantization_config", "_pre_quantization_dtype"):
        if attribute in attributes:
            delattr(stripped, attribute)
    return stripped


def adapter_base_model(checkpoint: str | Path) -> str | None:
    """The Hub id of the base model a PEFT adapter directory was trained on.

    Args:
        checkpoint: A saved checkpoint directory (unzipped archive).

    Returns:
        ``base_model_name_or_path`` from the adapter's config, or ``None``
        when the directory holds a full checkpoint (no adapter config, or
        one that names no base model).

    Notes:
        Reads ``adapter_config.json`` from disk (I/O).
    """
    path = Path(checkpoint) / ADAPTER_CONFIG_FILE
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as f:
        adapter: dict[str, Any] = json.load(f)
    base = adapter.get("base_model_name_or_path")
    return str(base) if base else None


# Minimum CUDA compute capability for native bfloat16 and for flash-attention
# 2 (both Ampere features; Turing and Volta have neither).
AMPERE: tuple[int, int] = (8, 0)


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """What the target accelerator can do, as far as the loading policy cares.

    Attributes:
        capability: CUDA compute capability ``(major, minor)`` of the first
            visible device, or ``None`` when no CUDA device is available.
        flash_attention: ``flash_attn`` is importable *and* the device can
            run its kernels.

    See Also:
        :func:`detect_device_profile`: Resolves the profile from the runtime.
    """

    capability: tuple[int, int] | None
    flash_attention: bool

    @property
    def cuda(self) -> bool:
        """A CUDA device is available."""
        return self.capability is not None

    @property
    def native_bf16(self) -> bool:
        """The device runs bfloat16 natively (Ampere or newer).

        Turing reports ``torch.cuda.is_bf16_supported()`` as ``True`` only
        through emulation, which is markedly slower than fp16 there.
        """
        return self.capability is not None and self.capability >= AMPERE

    @property
    def dtype(self) -> torch.dtype:
        """Half-precision dtype for weights and quantized-matmul compute.

        ``bfloat16`` where it is native, ``float16`` on older GPUs, and
        ``float32`` without CUDA.

        Gemma-2 was trained in bfloat16 and is known to overflow to NaN in
        float16, so on a Turing box (the T4 dev machine) the Gemma families
        are good for smoke tests only; the A100 target resolves to bfloat16
        and is unaffected. Qwen2.5 and Llama-3 train fine in float16.
        """
        if not self.cuda:
            return torch.float32
        return torch.bfloat16 if self.native_bf16 else torch.float16

    @property
    def attn_implementation(self) -> AttentionImplementation:
        """``"flash_attention_2"`` when usable, else PyTorch's built-in ``"sdpa"``."""
        return "flash_attention_2" if self.flash_attention else "sdpa"

    def describe(self) -> str:
        """One-line human-readable summary for the run logs."""
        dtype = str(self.dtype).removeprefix("torch.")
        if self.capability is None:
            return f"cpu (no CUDA device): {dtype}, {self.attn_implementation} attention"
        major, minor = self.capability
        return f"cuda sm_{major}{minor}: {dtype}, {self.attn_implementation} attention"


def detect_device_profile(*, device: int = 0) -> DeviceProfile:
    """Resolve the :class:`DeviceProfile` of the visible CUDA device.

    Args:
        device: Index among the visible CUDA devices (after
            ``CUDA_VISIBLE_DEVICES`` is applied) to inspect.

    Returns:
        The profile: compute capability (``None`` without CUDA) and whether
        flash-attention 2 is both installed and runnable on that device.

    Notes:
        Pure read of the runtime (``torch.cuda`` and ``importlib``); no
        device memory is allocated.
    """
    capability = torch.cuda.get_device_capability(device) if torch.cuda.is_available() else None
    flash_attention = (
        capability is not None and capability >= AMPERE and importlib.util.find_spec("flash_attn") is not None
    )
    return DeviceProfile(capability=capability, flash_attention=flash_attention)


def llm_model_args(profile: DeviceProfile | None = None) -> dict[str, Any]:
    """4-bit load arguments for decoder LLM classifiers, tuned to the device.

    Args:
        profile: The device to load for; resolved via
            :func:`detect_device_profile` when omitted.

    Returns:
        Keyword arguments for ``AutoModelForSequenceClassification.from_pretrained``:
        the profile's half-precision dtype (bf16 on Ampere+, fp16 on Turing)
        used both for the un-quantized weights and as the 4-bit NF4 compute
        dtype (:func:`reddit.modeling.peft.build_quantization_config`), the
        profile's attention implementation (flash-attention 2 where
        available, SDPA otherwise), and ``device_map="auto"`` (accelerate
        dispatch — required for both training, see
        :meth:`reddit.training.llms.LlmSeedStrategy.build_model`, and
        inference, see :func:`reddit.inference.llms.label_corpus`).
    """
    profile = profile or detect_device_profile()
    return {
        "dtype": profile.dtype,
        "quantization_config": build_quantization_config(compute_dtype=profile.dtype),
        "attn_implementation": profile.attn_implementation,
        "low_cpu_mem_usage": True,
        "device_map": "auto",
    }


def bert_model_args(profile: DeviceProfile | None = None) -> dict[str, Any]:
    """Load arguments for fully fine-tuned BERT-family encoders.

    Args:
        profile: Accepted for interface parity with :func:`llm_model_args`;
            the encoder policy does not currently depend on it.

    Returns:
        Keyword arguments for ``AutoModelForSequenceClassification.from_pretrained``:
        float32 weights only (no quantization, no PEFT — the encoder path is
        fully fine-tuned). Mixed precision is the Trainer's job, driven by
        the ``fp16``/``bf16`` flags in ``config.yml``: full fine-tuning under
        fp16 autocast needs fp32 master weights (``GradScaler`` refuses to
        unscale fp16 parameters), and the previous bfloat16 weights kept the
        optimizer state at an 8-bit mantissa. Inference casts to fp16
        separately (``CorpusJob.half``).
    """
    del profile  # the encoder policy is device-independent today
    return {"dtype": torch.float32}
