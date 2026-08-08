"""Fixtures shared by the ``reddit.inference`` sub-suite.

No real checkpoint is ever loaded: the model and tokenizer boundaries are
replaced by CPU-only doubles built on plain ``torch`` tensors.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import torch
from pandas import DataFrame

from reddit.core.config import Config
from reddit.inference.corpus import SUBREDDITS, CorpusJob


class FakeTokenizer:
    """Records its keyword arguments and returns constant-length tensors."""

    def __init__(self, sequence_length: int = 4) -> None:
        self.sequence_length = sequence_length
        self.calls: list[dict[str, Any]] = []

    def __call__(self, texts: list[str], **kwargs: Any) -> dict[str, torch.Tensor]:
        self.calls.append({"texts": list(texts), **kwargs})
        shape = (len(texts), self.sequence_length)
        return {
            "input_ids": torch.ones(shape, dtype=torch.long),
            "attention_mask": torch.ones(shape, dtype=torch.long),
        }


class FakeModel:
    """A classifier double whose argmax follows a preset, cycling sequence."""

    def __init__(
        self,
        *,
        predictions: Sequence[int] = (0,),
        num_classes: int = 3,
        hf_device_map: dict[str, int] | None = None,
        failing_batches: Sequence[int] = (),
    ) -> None:
        self.predictions = list(predictions)
        self.num_classes = num_classes
        self.failing_batches = set(failing_batches)
        self.moved_to: list[str] = []
        self.half_calls = 0
        self.eval_calls = 0
        self.batch_sizes: list[int] = []
        self._cursor = 0
        if hf_device_map is not None:
            self.hf_device_map = hf_device_map

    def __call__(self, *, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> SimpleNamespace:
        index = len(self.batch_sizes)
        rows = int(input_ids.shape[0])
        self.batch_sizes.append(rows)
        if index in self.failing_batches:
            raise RuntimeError("simulated CUDA failure")
        assert attention_mask.shape == input_ids.shape
        logits = torch.full((rows, self.num_classes), -5.0)
        for row in range(rows):
            logits[row, self.predictions[(self._cursor + row) % len(self.predictions)]] = 5.0
        self._cursor += rows
        return SimpleNamespace(logits=logits)

    def to(self, device: str) -> FakeModel:
        self.moved_to.append(str(device))
        return self

    def half(self) -> FakeModel:
        self.half_calls += 1
        return self

    def eval(self) -> FakeModel:
        self.eval_calls += 1
        return self

    def parameters(self) -> Iterator[torch.nn.Parameter]:
        return iter([torch.nn.Parameter(torch.zeros(1))])


@pytest.fixture
def fake_tokenizer() -> FakeTokenizer:
    """A tokenizer double returning fixed-width tensors."""
    return FakeTokenizer()


@pytest.fixture
def fake_model() -> FakeModel:
    """A model double predicting label id 0 for every row."""
    return FakeModel()


@pytest.fixture
def submissions_job(bootstrapped_config: Config) -> CorpusJob:
    """A BERT-flavoured submissions job (in-place answers update, text kept)."""
    return CorpusJob(
        name="submissions",
        desc="Predicting with tiny",
        csv_format="{}_final_jae.csv",
        output_filename="tiny_submissions_predicted_labels.csv",
        answer_file="all_final_jae.csv",
        dump_file=bootstrapped_config.paths.output_dir / "tiny_labelled_submissions.jsonl",
        label_col="tiny_label",
        trend_col="tiny_trend",
        text_col="title_sub",
        cols=("created_utc", "id_sub"),
        batch_size=bootstrapped_config.inference.batch_size,
        max_length=32,
        keep_text=True,
    )


@pytest.fixture
def write_corpus(bootstrapped_config: Config) -> Callable[..., dict[str, DataFrame]]:
    """Factory: write the per-subreddit submissions corpus CSVs.

    ``rows`` texts are written per subreddit; ``blank_rows`` extra rows carry an
    empty (NaN) text so the ``notna`` filter can be observed.
    """

    def _write(rows: int = 4, blank_rows: int = 0, text_col: str = "title_sub") -> dict[str, DataFrame]:
        written: dict[str, DataFrame] = {}
        for subreddit in SUBREDDITS:
            frame = DataFrame(
                {
                    "created_utc": [1_600_000_000 + i for i in range(rows + blank_rows)],
                    "id_sub": [f"{subreddit}_{i}" for i in range(rows + blank_rows)],
                    text_col: [f"{subreddit} text {i}" for i in range(rows)] + [None] * blank_rows,
                    "ignored_column": ["noise"] * (rows + blank_rows),
                }
            )
            frame.to_csv(
                bootstrapped_config.paths.reddit_dir / f"{subreddit}_final_jae.csv",
                index=False,
                encoding="utf-8",
            )
            written[subreddit] = frame
        return written

    return _write


@pytest.fixture
def write_answers(bootstrapped_config: Config) -> Callable[..., Path]:
    """Factory: write a consolidated answers CSV covering the whole corpus."""

    def _write(name: str = "all_final_jae.csv", rows: int = 4, extra: dict[str, list[Any]] | None = None) -> Path:
        created: list[int] = []
        ids: list[str] = []
        for subreddit in SUBREDDITS:
            created.extend(1_600_000_000 + i for i in range(rows))
            ids.extend(f"{subreddit}_{i}" for i in range(rows))
        payload: dict[str, Any] = {
            "created_utc": created,
            "id_sub": ids,
            "title_sub": [f"answers text {i}" for i in range(len(ids))],
        }
        if extra:
            payload.update(extra)
        target = bootstrapped_config.paths.results_dir / name
        DataFrame(payload).to_csv(target, index=False, encoding="utf-8")
        return target

    return _write
