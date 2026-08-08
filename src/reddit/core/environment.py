"""Process-environment bootstrap.

Replaces the boilerplate every ``legacy/scripts/launch_*.sh`` used to export
(``CUDA_VISIBLE_DEVICES``, ``HF_HOME``, ``PYTORCH_CUDA_ALLOC_CONF``) plus the
dotenv + Hugging Face Hub login each script performed.  Must run BEFORE torch
initializes CUDA, i.e. before the pipeline modules are imported.

Also the home of :func:`bootstrap_directories`, so that loading a
configuration stays free of filesystem side effects.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from reddit.core.config import Config, PathsConfig


def bootstrap_directories(config: Config) -> None:
    """Create every directory declared under ``paths:``.

    Previously done inside a pydantic field validator, which made merely
    loading a config create directories as a side effect.  Callers that only
    want to inspect a configuration no longer pay for that.
    """
    for name in PathsConfig.model_fields:
        directory: Path = getattr(config.paths, name)
        directory.mkdir(parents=True, exist_ok=True)


def prepare_environment(config: Config, gpu: str | None = None) -> None:
    """Export runtime env vars, load the dotenv file and log into the HF Hub.

    Args:
        config: Project configuration (``environment`` section).
        gpu: Value for ``CUDA_VISIBLE_DEVICES`` (e.g. ``"0"`` or ``"0,1"``);
            ``None`` leaves the current setting untouched.
    """
    if gpu is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = gpu

    env = config.environment
    if env.pytorch_alloc_conf:
        os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", env.pytorch_alloc_conf)
    if env.hf_home:
        os.environ.setdefault("HF_HOME", str(env.hf_home))
    os.environ.setdefault("HF_HOME", str(os.path.expanduser("~/.cache/huggingface")))

    if env.dotenv and env.dotenv.exists():
        load_dotenv(env.dotenv)

    token = os.environ.get("HUGGINGFACEHUB_API_TOKEN") or os.environ.get("HF_TOKEN")
    if token:
        # Export HF_TOKEN instead of calling `huggingface_hub.login(token)`:
        # login() persists the secret to $HF_HOME/token, and hf_home points at
        # a world-readable (mode 777) shared CIFS mount. The env var grants the
        # same hub access for this process tree without writing it to disk.
        os.environ["HF_TOKEN"] = token
    else:
        logging.warning("No Hugging Face token found (HUGGINGFACEHUB_API_TOKEN / HF_TOKEN); gated models will fail.")
