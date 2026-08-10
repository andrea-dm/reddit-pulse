"""Gold-dataset preparation: load, clean, stratified split, HF DatasetDict.

Prepares the hand-labelled seed dataset (``labelled.xlsx``) used as ground
truth for fine-tuning: each row carries a Reddit submission title and its
directional inflation-expectation label (``up``/``down``/``neutral``). This
module only *consumes* that file; the human-in-the-loop process that
produced its labels (manual annotation, zero-shot LLaMA-70B assistance,
fine-tuned LLaMA-8B classification and ChatGPT-assisted adjudication of
disagreements) is not part of this package.

See Also:
    :mod:`reddit.inference.corpus`: The full-corpus counterpart — labels
        every row of the (much larger) unlabelled subreddit CSVs with a
        checkpoint trained on the dataset this module prepares.
"""

# datasets and sklearn ship no type stubs; the Unknowns stay in this file.
# pyright: reportMissingTypeStubs=false, reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from datasets import ClassLabel, Dataset, DatasetDict
from pandas import DataFrame, read_excel
from sklearn.model_selection import train_test_split

from reddit.core.config import LabelsConfig
from reddit.core.errors import UndeclaredLabelError


@dataclass(frozen=True, slots=True)
class DataBundle:
    """The prepared gold dataset together with its label mapping.

    Attributes:
        dataset: Stratified ``train``/``validation``/``test`` splits, with a
            ``"text"`` column and a ``"label"`` ``ClassLabel`` column.
        num_labels: Number of distinct label classes (``3`` for the
            directional up/down/neutral task).
        id2label: Integer id to label-name mapping (``{0: "down", ...}``).
        label2id: Label-name to integer id mapping (the inverse of
            ``id2label``).
    """

    dataset: DatasetDict
    num_labels: int
    id2label: dict[int, str]
    label2id: dict[str, int]


def load_and_prepare_data(
    data_file_path: str | Path,
    text_column: str,
    label_column: str,
    test_size: float,
    validation_size: float,
    random_seed: int,
    labels: LabelsConfig,
) -> DataBundle:
    """Load the labelled Excel file, clean it, split it, and return a :class:`DataBundle`.

    The label mapping comes from ``labels``, not from the contents of the file.
    Deriving it from the data made the ids used at training time and the ids
    decoded at inference time two independent sources of truth that agreed only
    because alphabetical order happened to match the configured ordering.

    Args:
        data_file_path: Path to the hand-labelled gold Excel file
            (``config.dataset.path``, e.g. ``data/labelled.xlsx``).
        text_column: Column holding the submission text to classify.
        label_column: Column holding the directional label; values are
            lower-cased before being mapped through ``labels.label2id``.
        test_size: Fraction of the cleaned data held out as the test split.
        validation_size: Fraction of the cleaned data held out as the
            validation split (computed on the full dataset; internally
            rescaled against the post-test-split remainder).
        random_seed: Seed for both stratified splits — training reruns this
            per fine-tuning seed (see :func:`reddit.training.loop.run_seeds`),
            so each seed sees a different train/validation/test partition.
        labels: The single label authority (``config.labels``); supplies
            ``label2id``/``id2label`` and the declared label set.

    Returns:
        A :class:`DataBundle` with class-stratified train/validation/test
        splits and the resolved label mapping.

    Raises:
        UndeclaredLabelError: the file contains a label the config does not declare.

    Notes:
        Reads ``data_file_path`` from disk (I/O) via ``pandas.read_excel``.
    """
    df = read_excel(data_file_path)

    df = df.dropna(subset=[text_column, label_column])
    df = df[[text_column, label_column]].copy()
    df[label_column] = df[label_column].str.lower()

    label2id = labels.label2id
    id2label = labels.id2label

    undeclared = sorted(set(df[label_column].unique()) - set(label2id))
    if undeclared:
        raise UndeclaredLabelError(
            f"`{data_file_path}` contains labels not declared under `labels.labels`: "
            f"{', '.join(undeclared)}. Declared labels: {', '.join(sorted(label2id))}."
        )

    df.rename(columns={text_column: "text", label_column: "label_str"}, inplace=True)
    df["label"] = df["label_str"].map(label2id)

    # Rationale: sklearn is untyped; train_test_split returns DataFrames when
    # given a DataFrame.
    train_val_df, test_df = cast(
        "tuple[DataFrame, DataFrame]",
        train_test_split(
            df,
            test_size=test_size,
            stratify=df["label"],
            random_state=random_seed,
        ),
    )

    # Adjust validation size relative to the new training set
    val_size_adjusted = validation_size / (1 - test_size)

    train_df, val_df = cast(
        "tuple[DataFrame, DataFrame]",
        train_test_split(
            train_val_df,
            test_size=val_size_adjusted,
            stratify=train_val_df["label"],
            random_state=random_seed,
        ),
    )

    dataset_dict = DatasetDict(
        {
            "train": Dataset.from_pandas(train_df, preserve_index=False),
            "validation": Dataset.from_pandas(val_df, preserve_index=False),
            "test": Dataset.from_pandas(test_df, preserve_index=False),
        }
    )
    dataset_dict = dataset_dict.cast_column("label", ClassLabel(names=labels.names))

    return DataBundle(
        dataset=dataset_dict,
        num_labels=labels.num_labels,
        id2label=id2label,
        label2id=label2id,
    )
