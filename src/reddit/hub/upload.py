"""Staging a Hub repository folder and pushing it.

The staged folder is exactly what the repository will contain, so it can be
inspected (``reddit upload --dry-run``) before anything leaves the box.
"""

# huggingface_hub's download helpers are only partially typed; the Unknowns stay here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import logging
import re
from functools import partial
from pathlib import Path
from shutil import copy2, rmtree
from typing import TYPE_CHECKING, Any

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.errors import HfHubHTTPError

from reddit.core.errors import HubError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

# The pickled `TrainingArguments` names this box's directories and is
# published as the scrubbed `training_args.json` instead.
IGNORED_CHECKPOINT_FILES: frozenset[str] = frozenset({"training_args.bin"})
MODEL_CARD_FILE = "README.md"
COLLECTION_URL_PREFIX = "https://huggingface.co/collections/"
# License and use-policy files a base repository may ship (`LICENSE`,
# `LICENSE.txt`, `LICENSE.md`, `USE_POLICY.md`, `NOTICE`); redistributing a
# derivative usually requires carrying them along.
LICENSE_FILE_PATTERN = re.compile(r"^(LICENSE|LICENCE|NOTICE|USE_POLICY)(\.[A-Za-z]+)?$", re.IGNORECASE)


def stage_checkpoint(checkpoint: Path, staging: Path, *, card: str, companions: Mapping[str, str]) -> list[str]:
    """Assemble the folder that becomes the Hub repository.

    Args:
        checkpoint: The selected checkpoint directory (adapter or full
            weights, tokenizer, ``config.json``).
        staging: Destination folder; replaced if it exists.
        card: The rendered model card, written as ``README.md``.
        companions: Extra text files, ``{relative name: content}``
            (``training_args.json``, ``training_config.yml``,
            ``evaluation/*.csv``).

    Returns:
        The staged files, as paths relative to ``staging``, sorted.

    Notes:
        Deletes and recreates ``staging``; copies every regular file of
        ``checkpoint`` except :data:`IGNORED_CHECKPOINT_FILES` (I/O).
    """
    if staging.exists():
        rmtree(staging)
    staging.mkdir(parents=True)
    for file in sorted(p for p in checkpoint.iterdir() if p.is_file() and p.name not in IGNORED_CHECKPOINT_FILES):
        copy2(file, staging / file.name)
    (staging / MODEL_CARD_FILE).write_text(card, encoding="utf-8")
    for name, content in companions.items():
        target = staging / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return sorted(str(p.relative_to(staging)) for p in staging.rglob("*") if p.is_file())


def publish(staging: Path, repo_id: str, *, private: bool, token: str | None, api: Any | None = None) -> str:
    """Create the repository if needed and upload the staged folder.

    Args:
        staging: The folder :func:`stage_checkpoint` assembled.
        repo_id: ``namespace/name`` of the target model repository.
        private: Create the repository as private (an existing repository
            keeps its visibility).
        token: A Hub token with *write* access; the read token that
            suffices for downloads cannot create or push to a repository.
        api: An ``HfApi``-like client, injected by tests; built from
            ``token`` when omitted.

    Returns:
        The URL of the commit that landed the upload (the repository URL
        when the client reports none).

    Raises:
        HubError: no token was given, or the Hub rejected the request.

    Notes:
        Network I/O: creates a repository and uploads every staged file.
    """
    if not token:
        raise HubError(
            "No Hugging Face token found (HF_WRITE_TOKEN, or HF_TOKEN, in the dotenv); "
            "uploading needs a token with write access."
        )
    client: Any = api if api is not None else HfApi(token=token)
    try:
        client.create_repo(repo_id, repo_type="model", private=private, exist_ok=True)
        result: Any = client.upload_folder(
            repo_id=repo_id,
            folder_path=str(staging),
            repo_type="model",
            commit_message=f"Upload {staging.name}",
        )
    except (HfHubHTTPError, OSError) as e:
        raise HubError(f"Uploading `{repo_id}` failed: {e}") from e
    return str(getattr(result, "commit_url", None) or f"https://huggingface.co/{repo_id}")


def resolve_collection(reference: str, *, token: str | None = None, api: Any | None = None) -> str:
    """The full ``owner/name-<id>`` slug of a collection.

    Args:
        reference: The slug with its id, the slug without it (as it appears
            in the collection's URL), or the URL itself.
        token: Hub token (a private collection needs it).
        api: An ``HfApi``-like client, injected by tests.

    Returns:
        The slug ``add_collection_item`` accepts.

    Raises:
        HubError: the reference is malformed, or it does not resolve to
            exactly one of the owner's collections.

    Notes:
        Network I/O: one lookup, plus a listing of the owner's collections
        when the reference carries no id.
    """
    slug = reference.removeprefix(COLLECTION_URL_PREFIX).strip("/")
    owner, _, name = slug.partition("/")
    if not owner or not name:
        raise HubError(f"`{reference}` is not a collection slug (`owner/name`) or URL.")
    client: Any = api if api is not None else HfApi(token=token)
    try:
        return str(client.get_collection(slug).slug)
    except (HfHubHTTPError, OSError):
        pass  # not a full slug: look the name up among the owner's collections
    try:
        candidates = [str(c.slug) for c in client.list_collections(owner=owner)]
    except (HfHubHTTPError, OSError) as e:
        raise HubError(f"Could not list the collections of `{owner}`: {e}") from e
    matches = [c for c in candidates if c == slug or c.startswith(f"{slug}-")]
    if len(matches) != 1:
        raise HubError(f"Collection `{slug}` does not resolve to exactly one of `{owner}`'s collections: {candidates}")
    return matches[0]


def add_to_collection(repo_id: str, collection: str, *, token: str | None, api: Any | None = None) -> str:
    """Add a published model repository to a collection.

    Args:
        repo_id: ``namespace/name`` of the repository.
        collection: Slug or URL of the collection (see :func:`resolve_collection`).
        token: A Hub token with write access to the collection.
        api: An ``HfApi``-like client, injected by tests.

    Returns:
        The full slug of the collection the repository was added to.

    Raises:
        HubError: the collection cannot be resolved or the Hub rejected the
            request.

    Notes:
        Network I/O. Adding a repository already in the collection is a
        no-op (``exists_ok``).
    """
    client: Any = api if api is not None else HfApi(token=token)
    slug = resolve_collection(collection, token=token, api=client)
    try:
        client.add_collection_item(slug, item_id=repo_id, item_type="model", exists_ok=True)
    except (HfHubHTTPError, OSError) as e:
        raise HubError(f"Adding `{repo_id}` to collection `{slug}` failed: {e}") from e
    return slug


def base_license_files(
    base_model: str,
    *,
    token: str | None = None,
    api: Any | None = None,
    download: Callable[[str, str], str] | None = None,
) -> dict[str, str]:
    """The license and use-policy files the base repository ships, by name.

    Llama and Apache-licensed bases ship ``LICENSE.txt``/``USE_POLICY.md`` or
    ``LICENSE`` whose redistribution their terms require; Gemma keeps its
    terms on the Hub page and ships nothing.

    Args:
        base_model: Hub id of the base checkpoint.
        token: Hub token (gated bases need it).
        api: An ``HfApi``-like client, injected by tests.
        download: ``(repo_id, filename) -> local path`` fetcher, injected by
            tests; ``hf_hub_download`` when omitted.

    Returns:
        ``{filename: text}`` for every matching file that could be read;
        empty when the repository ships none or cannot be reached (logged).

    Notes:
        Network I/O: one file listing and one download per matching file
        (into the Hub cache).
    """
    client: Any = api if api is not None else HfApi(token=token)
    fetch = download if download is not None else partial(hf_hub_download, token=token)
    try:
        names = [name for name in client.list_repo_files(base_model) if LICENSE_FILE_PATTERN.match(name)]
    except (HfHubHTTPError, OSError) as e:
        logging.warning("Could not list the files of `%s` for its license: %s", base_model, e)
        return {}
    files: dict[str, str] = {}
    for name in names:
        try:
            files[name] = Path(fetch(base_model, name)).read_text(encoding="utf-8")
        except (HfHubHTTPError, OSError, UnicodeDecodeError) as e:
            logging.warning("Could not fetch `%s` from `%s`: %s", name, base_model, e)
    return files


def base_model_license(base_model: str, *, token: str | None = None, api: Any | None = None) -> str | None:
    """The license id the base model's own card declares, if it declares one.

    Args:
        base_model: Hub id of the base checkpoint.
        token: Hub token (gated bases need it to be readable).
        api: An ``HfApi``-like client, injected by tests.

    Returns:
        The ``license`` metadata value (``apache-2.0``, ``gemma``,
        ``llama3.2``, ...), or ``None`` when the card has none or the Hub
        could not be reached — the card is then published without a
        license field, for the reviewer to fill in.

    Notes:
        Network I/O (one metadata request); failures are logged, not raised.
    """
    client: Any = api if api is not None else HfApi(token=token)
    try:
        card_data: Any = client.model_info(base_model).card_data
    except (HfHubHTTPError, OSError) as e:
        logging.warning("Could not read the license of `%s` from the Hub: %s", base_model, e)
        return None
    value = card_data.get("license") if card_data else None
    return str(value) if value else None
