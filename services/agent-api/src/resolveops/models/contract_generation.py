"""Generate deterministic frontend contract artifacts from Pydantic models."""

from __future__ import annotations

import argparse
import json
import types
from collections.abc import Sequence
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, TypeVar, Union, get_args, get_origin
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, JsonValue

from resolveops.models.contracts import (
    ActionExecutionStatus,
    ActionProposal,
    ActionProposalInput,
    ActionResult,
    ActionType,
    ApprovalDecision,
    ApprovalDecisionType,
    ApprovalRequest,
    ArtifactKind,
    AttachmentMetadata,
    CaseCategory,
    CaseClassification,
    CaseStatus,
    EvidenceBundle,
    EvidenceItem,
    FinalResponse,
    InvestigationPlan,
    ProposalStatus,
    ReadToolName,
    RequestedToolCall,
    ResolutionProposal,
    RiskIndicator,
    RiskLevel,
    RunArtifact,
    RunError,
    RunStatus,
    SourceSystem,
    SupportCase,
    TicketInput,
    ToolResult,
    Urgency,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowRun,
)

CONTRACT_ENUMS: tuple[type[StrEnum], ...] = (
    SourceSystem,
    CaseCategory,
    Urgency,
    RiskIndicator,
    ReadToolName,
    ActionType,
    RiskLevel,
    ProposalStatus,
    ApprovalDecisionType,
    ActionExecutionStatus,
    RunStatus,
    CaseStatus,
    WorkflowEventType,
    ArtifactKind,
)

CONTRACT_MODELS: tuple[type[BaseModel], ...] = (
    AttachmentMetadata,
    TicketInput,
    CaseClassification,
    InvestigationPlan,
    RequestedToolCall,
    EvidenceItem,
    EvidenceBundle,
    ActionProposalInput,
    ResolutionProposal,
    ActionProposal,
    ApprovalDecision,
    ApprovalRequest,
    ActionResult,
    FinalResponse,
    ToolResult,
    RunError,
    SupportCase,
    WorkflowRun,
    WorkflowEvent,
    RunArtifact,
)


def _openapi_document() -> str:
    schemas: dict[str, Any] = {}
    for model in CONTRACT_MODELS:
        schema = model.model_json_schema(
            by_alias=True,
            ref_template="#/components/schemas/{model}",
        )
        definitions = schema.pop("$defs", {})
        for name, definition in definitions.items():
            existing = schemas.get(name)
            if existing is not None and existing != definition:
                raise ValueError(f"conflicting generated schema for {name}")
            schemas[name] = definition
        schemas[model.__name__] = schema

    document = {
        "openapi": "3.1.0",
        "info": {
            "title": "ResolveOps shared contracts",
            "version": "0.1.0",
            "description": "Generated from Pydantic v2 models; do not edit by hand.",
        },
        "paths": {},
        "components": {"schemas": schemas},
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def _typescript_type(annotation: Any) -> str:
    if annotation is JsonValue:
        return "JsonValue"
    if isinstance(annotation, TypeVar):
        return annotation.__name__
    if annotation in (str, UUID, datetime, date, AwareDatetime):
        return "string"
    if annotation in (int, float):
        return "number"
    if annotation is bool:
        return "boolean"
    if annotation in (Any, object):
        return "unknown"
    if annotation is type(None):
        return "null"

    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is Annotated:
        return _typescript_type(arguments[0])
    if origin in (Union, types.UnionType):
        return " | ".join(dict.fromkeys(_typescript_type(item) for item in arguments))
    if origin in (list, set, tuple, Sequence):
        item_type = _typescript_type(arguments[0]) if arguments else "unknown"
        return f"Array<{item_type}>"
    if origin is dict:
        value_type = _typescript_type(arguments[1]) if len(arguments) == 2 else "unknown"
        return f"Record<string, {value_type}>"
    if isinstance(annotation, type) and issubclass(annotation, (BaseModel, StrEnum)):
        return annotation.__name__
    raise TypeError(f"unsupported contract annotation: {annotation!r}")


def _typescript_document() -> str:
    lines = [
        "/** Generated from ResolveOps Pydantic contracts. Do not edit by hand. */",
        "",
        "export type JsonValue =",
        "  | string",
        "  | number",
        "  | boolean",
        "  | null",
        "  | JsonValue[]",
        "  | { [key: string]: JsonValue };",
        "",
    ]
    for enum_type in CONTRACT_ENUMS:
        values = " | ".join(json.dumps(member.value) for member in enum_type)
        lines.extend((f"export type {enum_type.__name__} = {values};", ""))

    for model in CONTRACT_MODELS:
        parameters = getattr(model, "__type_params__", ())
        generic_suffix = ""
        if parameters:
            names = ", ".join(f"{parameter.__name__} = JsonValue" for parameter in parameters)
            generic_suffix = f"<{names}>"
        lines.append(f"export interface {model.__name__}{generic_suffix} {{")
        for field_name, field in model.model_fields.items():
            optional = "" if field.is_required() else "?"
            lines.append(f"  {field_name}{optional}: {_typescript_type(field.annotation)};")
        lines.extend(("}", ""))
    return "\n".join(lines)


def contract_artifacts(repository_root: Path) -> dict[Path, str]:
    package_root = repository_root / "packages/contracts"
    return {
        package_root / "openapi.json": _openapi_document(),
        package_root / "generated/index.ts": _typescript_document(),
    }


def generate_contracts(repository_root: Path, *, check: bool) -> bool:
    """Write artifacts, or return whether all existing artifacts are current."""

    artifacts = contract_artifacts(repository_root)
    if check:
        stale = [
            path
            for path, expected in artifacts.items()
            if not path.exists() or path.read_text(encoding="utf-8") != expected
        ]
        if stale:
            for path in stale:
                print(f"generated contract is stale: {path}")
            return False
        return True

    for path, content in artifacts.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when generated files differ")
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[5],
    )
    arguments = parser.parse_args()
    return (
        0 if generate_contracts(arguments.repository_root.resolve(), check=arguments.check) else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
