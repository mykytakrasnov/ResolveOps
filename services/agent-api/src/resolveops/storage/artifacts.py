"""Safe object-storage boundary for private run report artifacts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

MAX_ARTIFACT_BYTES = 1024 * 1024
_OBJECT_KEY = re.compile(
    r"^runs/(?P<run_id>[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/"
    r"(?P<name>report\.json|report\.md|customer-response\.txt|events\.jsonl)$"
)
_MIME_BY_NAME = {
    "report.json": "application/json",
    "report.md": "text/markdown; charset=utf-8",
    "customer-response.txt": "text/plain; charset=utf-8",
    "events.jsonl": "application/x-ndjson",
}


@dataclass(frozen=True)
class StoredObject:
    object_key: str
    mime_type: str
    sha256: str
    size_bytes: int


@runtime_checkable
class ObjectStorage(Protocol):
    def put_object(
        self,
        *,
        object_key: str,
        content: bytes,
        mime_type: str,
    ) -> StoredObject: ...

    def get_object(self, *, object_key: str) -> RetrievedObject | None: ...


@dataclass(frozen=True)
class InMemoryObject:
    content: bytes
    object_key: str
    mime_type: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class RetrievedObject:
    content: bytes
    object_key: str
    mime_type: str
    sha256: str
    size_bytes: int


def _validate_object(
    *,
    object_key: str,
    content: bytes,
    mime_type: str,
) -> StoredObject:
    match = _OBJECT_KEY.fullmatch(object_key)
    if match is None:
        raise ValueError("object key must identify an allowlisted run report")
    UUID(match.group("run_id"))
    expected_mime = _MIME_BY_NAME[match.group("name")]
    if mime_type != expected_mime:
        raise ValueError("object MIME type does not match the allowlisted report kind")
    if len(content) > MAX_ARTIFACT_BYTES:
        raise ValueError("object content exceeds the report size limit")
    return StoredObject(
        object_key=object_key,
        mime_type=mime_type,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )


class LocalObjectStorage:
    """Private local storage for development; keys cannot escape the configured root."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._root.chmod(0o700)

    def put_object(
        self,
        *,
        object_key: str,
        content: bytes,
        mime_type: str,
    ) -> StoredObject:
        stored = _validate_object(
            object_key=object_key,
            content=content,
            mime_type=mime_type,
        )
        destination = (self._root / object_key).resolve()
        if not destination.is_relative_to(
            self._root
        ):  # pragma: no cover - key validator is stricter
            raise ValueError("object key resolves outside the storage root")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = destination.with_name(f".{destination.name}.{uuid4()}.tmp")
        try:
            temporary.write_bytes(content)
            temporary.chmod(0o600)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return stored

    def get_object(self, *, object_key: str) -> RetrievedObject | None:
        mime_type = _mime_type_for_key(object_key)
        destination = (self._root / object_key).resolve()
        if not destination.is_relative_to(self._root):
            raise ValueError("object key resolves outside the storage root")
        if not destination.is_file():
            return None
        content = destination.read_bytes()
        stored = _validate_object(
            object_key=object_key,
            content=content,
            mime_type=mime_type,
        )
        return RetrievedObject(content=content, **stored.__dict__)


class InMemoryObjectStorage:
    """Deterministic test double implementing the same bounded object rules."""

    def __init__(self) -> None:
        self.objects: dict[str, InMemoryObject] = {}

    def put_object(
        self,
        *,
        object_key: str,
        content: bytes,
        mime_type: str,
    ) -> StoredObject:
        stored = _validate_object(
            object_key=object_key,
            content=content,
            mime_type=mime_type,
        )
        self.objects[object_key] = InMemoryObject(content=content, **stored.__dict__)
        return stored

    def get_object(self, *, object_key: str) -> RetrievedObject | None:
        mime_type = _mime_type_for_key(object_key)
        stored = self.objects.get(object_key)
        if stored is None:
            return None
        validated = _validate_object(
            object_key=object_key,
            content=stored.content,
            mime_type=mime_type,
        )
        return RetrievedObject(content=stored.content, **validated.__dict__)


def _mime_type_for_key(object_key: str) -> str:
    match = _OBJECT_KEY.fullmatch(object_key)
    if match is None:
        raise ValueError("object key must identify an allowlisted run report")
    return _MIME_BY_NAME[match.group("name")]
