import os
import subprocess
import sys
from pathlib import Path

import pytest

from resolveops.models.contract_generation import CONTRACT_MODELS, generate_contracts


def test_all_required_workflow_contracts_are_published() -> None:
    published_names = {model.__name__ for model in CONTRACT_MODELS}

    assert {
        "TicketInput",
        "CaseClassification",
        "InvestigationPlan",
        "RequestedToolCall",
        "EvidenceItem",
        "EvidenceBundle",
        "ResolutionProposal",
        "ActionProposalInput",
        "ApprovalDecision",
        "ActionResult",
        "FinalResponse",
        "ToolResult",
        "RunError",
    } <= published_names


def test_contract_drift_check_detects_a_modified_artifact(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert generate_contracts(tmp_path, check=False)
    assert generate_contracts(tmp_path, check=True)

    generated_types = tmp_path / "packages/contracts/generated/index.ts"
    generated_types.write_text("// stale\n", encoding="utf-8")

    assert not generate_contracts(tmp_path, check=True)
    capsys.readouterr()


def test_generated_contracts_are_current() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repository_root / "services/agent-api/src")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "resolveops.models.contract_generation",
            "--check",
            "--repository-root",
            str(repository_root),
        ],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
