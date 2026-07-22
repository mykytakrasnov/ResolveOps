from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from resolveops.storage.artifacts import InMemoryObjectStorage, LocalObjectStorage


def test_local_object_storage_writes_bounded_content_and_returns_integrity_metadata(
    tmp_path: Path,
) -> None:
    storage = LocalObjectStorage(tmp_path)
    content = b'{"synthetic":true}\n'

    stored = storage.put_object(
        object_key="runs/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/report.json",
        content=content,
        mime_type="application/json",
    )

    assert (tmp_path / stored.object_key).read_bytes() == content
    assert stored.sha256 == hashlib.sha256(content).hexdigest()
    assert stored.size_bytes == len(content)
    assert stored.mime_type == "application/json"


@pytest.mark.parametrize(
    "object_key",
    ["/tmp/report.json", "../report.json", "runs/../../report.json", "other/report.json"],
)
def test_object_storage_rejects_keys_outside_the_bounded_runs_prefix(object_key: str) -> None:
    storage = InMemoryObjectStorage()

    with pytest.raises(ValueError, match="object key"):
        storage.put_object(
            object_key=object_key,
            content=b"{}",
            mime_type="application/json",
        )


def test_object_storage_rejects_mime_mismatch_and_oversized_content() -> None:
    storage = InMemoryObjectStorage()
    object_key = "runs/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/report.json"

    with pytest.raises(ValueError, match="MIME type"):
        storage.put_object(object_key=object_key, content=b"{}", mime_type="text/plain")
    with pytest.raises(ValueError, match="size limit"):
        storage.put_object(
            object_key=object_key,
            content=b"x" * (1024 * 1024 + 1),
            mime_type="application/json",
        )
