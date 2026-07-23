from __future__ import annotations

from pathlib import Path

import pytest

from resolveops.api.runs import require_principal
from resolveops.local_dev import (
    LOCAL_ORGANIZATION_ID,
    LOCAL_USER_ID,
    create_local_app,
    load_public_cases,
)
from resolveops.synthetic_data import build_dataset


def test_load_public_cases_reads_only_valid_synthetic_case_files(tmp_path: Path) -> None:
    case = build_dataset().public_cases[0]
    case_root = tmp_path / "synthetic" / "v1" / "cases" / "public"
    case_root.mkdir(parents=True)
    (case_root / f"{case.case_id}.json").write_text(case.model_dump_json(), encoding="utf-8")

    loaded = load_public_cases(tmp_path)

    assert loaded == [case]


def test_load_public_cases_rejects_a_missing_dataset(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Generate it before starting local development"):
        load_public_cases(tmp_path)


def test_local_app_uses_bounded_synthetic_operator_and_reviewer_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL_POOLED",
        "postgresql+psycopg://resolveops:resolveops@127.0.0.1:5432/resolveops",
    )

    application = create_local_app()
    principal = application.dependency_overrides[require_principal]()

    assert principal.organization_id == LOCAL_ORGANIZATION_ID
    assert principal.user_id == LOCAL_USER_ID
    assert principal.roles == frozenset({"operator", "reviewer"})
