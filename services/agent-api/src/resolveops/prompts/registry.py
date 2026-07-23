"""Local prompt templates used when remote prompt management is unavailable."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROMPT_BUNDLE_VERSION = "1.0.0"
_PROMPT_VERSIONS = {
    "resolveops/classify-case": 1,
    "resolveops/assess-evidence-gaps": 1,
    "resolveops/propose-resolution": 1,
    "resolveops/draft-response": 1,
}


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: int
    bundle_version: str
    content: str

    def render(self, variables: dict[str, Any]) -> str:
        required = set(re.findall(r"\{\{([a-z][a-z0-9_]*)\}\}", self.content))
        missing = required.difference(variables)
        if missing:
            raise ValueError(f"missing prompt variables: {', '.join(sorted(missing))}")
        rendered = self.content
        for name in required:
            value = variables[name]
            encoded = (
                value
                if isinstance(value, str)
                else json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
            )
            rendered = rendered.replace(f"{{{{{name}}}}}", encoded)
        return rendered


class PromptRegistry:
    """Resolve exact local prompt versions without accepting arbitrary paths."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or Path(__file__).with_name("v1")

    def get(self, name: str) -> PromptTemplate:
        try:
            version = _PROMPT_VERSIONS[name]
        except KeyError as error:
            raise KeyError(f"unknown prompt name: {name}") from error
        filename = name.removeprefix("resolveops/").replace("-", "_")
        content = (self._root / f"{filename}.md").read_text(encoding="utf-8")
        return PromptTemplate(
            name=name,
            version=version,
            bundle_version=PROMPT_BUNDLE_VERSION,
            content=content,
        )
