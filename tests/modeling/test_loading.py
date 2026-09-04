"""Contract tests for :mod:`reddit.modeling.loading`.

This module is the single owner of *how* a checkpoint is materialised; the
:class:`DeviceProfile` it resolves and the argument dictionaries it derives
from it are its entire public contract.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from typing import Any

import pytest
import torch

from reddit.modeling import loading
from reddit.modeling.loading import (
    AMPERE,
    BERT_MAX_LENGTH,
    LLM_MAX_LENGTH,
    DeviceProfile,
    bert_model_args,
    detect_device_profile,
    llm_model_args,
)

TURING = DeviceProfile(capability=(7, 5), flash_attention=False)
AMPERE_WITH_FLASH = DeviceProfile(capability=(8, 0), flash_attention=True)
AMPERE_WITHOUT_FLASH = DeviceProfile(capability=(8, 6), flash_attention=False)
HOPPER = DeviceProfile(capability=(9, 0), flash_attention=True)
CPU = DeviceProfile(capability=None, flash_attention=False)

GPU_PROFILES = [TURING, AMPERE_WITH_FLASH, AMPERE_WITHOUT_FLASH, HOPPER]
ALL_PROFILES = [*GPU_PROFILES, CPU]


class TestLoadingModule:
    """Truncation lengths, device profile and model-loading policy."""

    @pytest.mark.unit
    class TestUnits:
        def test_truncation_lengths_match_the_two_architectures(self) -> None:
            assert LLM_MAX_LENGTH == 1024
            assert BERT_MAX_LENGTH == 512

        # ───────────────────────────────────────────────────── DeviceProfile ──

        def test_native_bf16_and_flash_attention_start_at_ampere(self) -> None:
            assert AMPERE == (8, 0)

        def test_turing_runs_fp16_through_sdpa(self) -> None:
            assert TURING.cuda is True
            assert TURING.native_bf16 is False
            assert TURING.dtype is torch.float16
            assert TURING.attn_implementation == "sdpa"

        @pytest.mark.parametrize("profile", [AMPERE_WITH_FLASH, HOPPER])
        def test_ampere_or_newer_runs_bf16_through_flash_attention(self, profile: DeviceProfile) -> None:
            assert profile.native_bf16 is True
            assert profile.dtype is torch.bfloat16
            assert profile.attn_implementation == "flash_attention_2"

        def test_ampere_without_the_package_falls_back_to_sdpa(self) -> None:
            assert AMPERE_WITHOUT_FLASH.dtype is torch.bfloat16
            assert AMPERE_WITHOUT_FLASH.attn_implementation == "sdpa"

        def test_a_cpu_only_host_runs_fp32_through_sdpa(self) -> None:
            assert CPU.cuda is False
            assert CPU.native_bf16 is False
            assert CPU.dtype is torch.float32
            assert CPU.attn_implementation == "sdpa"

        @pytest.mark.parametrize(
            ("capability", "native"),
            [((7, 0), False), ((7, 5), False), ((8, 0), True), ((8, 9), True), ((12, 0), True)],
        )
        def test_native_bf16_is_a_capability_threshold(self, capability: tuple[int, int], native: bool) -> None:
            assert DeviceProfile(capability=capability, flash_attention=False).native_bf16 is native

        def test_the_profile_is_immutable(self) -> None:
            with pytest.raises(AttributeError):
                TURING.flash_attention = True  # pyright: ignore[reportAttributeAccessIssue]

        @pytest.mark.parametrize("profile", ALL_PROFILES)
        def test_the_description_names_the_dtype_and_the_attention(self, profile: DeviceProfile) -> None:
            described = profile.describe()

            assert str(profile.dtype).removeprefix("torch.") in described
            assert profile.attn_implementation in described

        def test_the_description_names_the_compute_capability(self) -> None:
            assert "sm_75" in TURING.describe()
            assert "cpu" in CPU.describe()

        # ─────────────────────────────────────────────────── llm_model_args ──

        @pytest.mark.parametrize("profile", GPU_PROFILES)
        def test_decoder_llms_are_loaded_quantized_in_the_profile_dtype(self, profile: DeviceProfile) -> None:
            args = llm_model_args(profile)

            assert args["dtype"] is profile.dtype
            assert args["quantization_config"].load_in_4bit is True
            assert args["quantization_config"].bnb_4bit_compute_dtype is profile.dtype
            assert args["attn_implementation"] == profile.attn_implementation
            assert args["low_cpu_mem_usage"] is True
            assert args["device_map"] == "auto"

        def test_the_llm_policy_declares_nothing_else(self) -> None:
            assert set(llm_model_args(TURING)) == {
                "dtype",
                "quantization_config",
                "attn_implementation",
                "low_cpu_mem_usage",
                "device_map",
            }

        def test_the_quantized_compute_dtype_never_disagrees_with_the_weights(self) -> None:
            """The previous fixed fp16 compute dtype under bf16 weights was the bug."""
            for profile in GPU_PROFILES:
                args = llm_model_args(profile)

                assert args["quantization_config"].bnb_4bit_compute_dtype is args["dtype"]

        def test_each_llm_call_builds_a_fresh_quantization_config(self) -> None:
            assert llm_model_args(TURING)["quantization_config"] is not llm_model_args(TURING)["quantization_config"]

        def test_an_omitted_profile_is_detected_from_the_runtime(self, monkeypatch: pytest.MonkeyPatch) -> None:
            monkeypatch.setattr(loading, "detect_device_profile", lambda: TURING)

            assert llm_model_args()["dtype"] is torch.float16
            assert llm_model_args()["attn_implementation"] == "sdpa"

        # ──────────────────────────────────────────────────── bert_model_args ──

        @pytest.mark.parametrize("profile", ALL_PROFILES)
        def test_encoders_are_loaded_in_fp32_without_quantization(self, profile: DeviceProfile) -> None:
            """Mixed precision is the Trainer's job; fp16 autocast needs fp32 master weights."""
            assert bert_model_args(profile) == {"dtype": torch.float32}

        def test_encoders_are_never_dispatched_by_accelerate(self) -> None:
            """``device_map`` would make ``_prepare_model_device`` skip the ``.to()``."""
            assert "device_map" not in bert_model_args(TURING)
            assert "quantization_config" not in bert_model_args(TURING)

        @pytest.mark.parametrize("factory", [llm_model_args, bert_model_args])
        def test_each_call_returns_an_independent_mapping(
            self, factory: Callable[[DeviceProfile], dict[str, Any]]
        ) -> None:
            first = factory(TURING)
            first["dtype"] = "corrupted"

            assert isinstance(factory(TURING)["dtype"], torch.dtype)

        def test_the_two_policies_never_share_a_dictionary(self) -> None:
            assert llm_model_args(TURING) is not bert_model_args(TURING)

        # ────────────────────────────────────────────── detect_device_profile ──

        def test_detection_without_cuda_yields_the_cpu_profile(self, monkeypatch: pytest.MonkeyPatch) -> None:
            monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

            assert detect_device_profile() == CPU

        @pytest.mark.parametrize(
            ("capability", "installed", "flash"),
            [((7, 5), True, False), ((7, 5), False, False), ((8, 0), False, False), ((8, 0), True, True)],
        )
        def test_flash_attention_needs_both_the_package_and_ampere(
            self, monkeypatch: pytest.MonkeyPatch, capability: tuple[int, int], installed: bool, flash: bool
        ) -> None:
            def find_spec(name: str) -> object | None:
                return object() if installed else None

            monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
            monkeypatch.setattr(torch.cuda, "get_device_capability", lambda device=0: capability)
            monkeypatch.setattr(importlib.util, "find_spec", find_spec)

            assert detect_device_profile() == DeviceProfile(capability=capability, flash_attention=flash)

    @pytest.mark.integration
    class TestIntegration:
        def test_detection_matches_the_visible_runtime(self) -> None:
            profile = detect_device_profile()

            if torch.cuda.is_available():
                assert profile.capability == torch.cuda.get_device_capability(0)
            else:
                assert profile == CPU
            if profile.flash_attention:
                assert importlib.util.find_spec("flash_attn") is not None
                assert profile.native_bf16
