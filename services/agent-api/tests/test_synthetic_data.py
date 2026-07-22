import hashlib
import json
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from resolveops.models.contracts import CaseCategory, WorkflowEvent, WorkflowEventType
from resolveops.synthetic_data import (
    DEFAULT_SEED,
    CrmUser,
    DatasetManifest,
    GroundTruth,
    Split,
    build_dataset,
    generate_dataset,
    validate_dataset,
)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(scope="module")
def generated_roots(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("generated-dataset")
    first = root / "first"
    second = root / "second"

    generate_dataset(first, seed=DEFAULT_SEED)
    generate_dataset(second, seed=DEFAULT_SEED)
    return first, second


def test_same_seed_generates_byte_identical_dataset(
    generated_roots: tuple[Path, Path],
) -> None:
    first, second = generated_roots

    assert _snapshot(first) == _snapshot(second)
    assert (first / "synthetic/v1/manifest.json").is_file()


def test_curated_duplicate_charge_case_has_separate_coherent_truth(
    generated_roots: tuple[Path, Path],
) -> None:
    dataset_root = generated_roots[0] / "synthetic/v1"
    public_cases = [
        json.loads(path.read_text())
        for path in sorted((dataset_root / "cases/public").glob("*.json"))
    ]
    duplicate_case = next(
        case for case in public_cases if case["category"] == "duplicate_charge" and case["curated"]
    )

    assert all("hidden_truth" not in case for case in public_cases)
    assert all("resolution_code" not in case for case in public_cases)
    assert all("expected_evidence_ids" not in case for case in public_cases)
    assert duplicate_case["expected_approval_required"] is True
    truth_path = dataset_root / "cases/ground-truth" / f"{duplicate_case['case_id']}.yaml"
    truth = json.loads(truth_path.read_text())
    assert truth["case_id"] == duplicate_case["case_id"]
    assert truth["resolution_code"] == "duplicate_charge_confirmed"
    assert truth["approval_required"] is True

    no_approval_case = next(
        case
        for case in public_cases
        if case["curated"] and case["case_id"] != duplicate_case["case_id"]
    )
    no_approval_truth = json.loads(
        (dataset_root / "cases/ground-truth" / f"{no_approval_case['case_id']}.yaml").read_text()
    )
    assert no_approval_case["expected_approval_required"] is False
    assert no_approval_truth["approval_required"] is False

    invoice_id = truth["expected_invoice_id"]
    invoice = json.loads((dataset_root / "billing/invoices" / f"{invoice_id}.json").read_text())
    crm_account = next(
        json.loads(path.read_text())
        for path in sorted((dataset_root / "crm/accounts").glob("*.json"))
        if json.loads(path.read_text())["customer_reference"]
        == duplicate_case["customer_reference"]
    )
    subscription = json.loads(
        (dataset_root / "billing/accounts" / f"{crm_account['account_id']}.json").read_text()
    )
    attempts = [
        json.loads(path.read_text())
        for path in sorted((dataset_root / "billing/payment-attempts").glob("*.json"))
        if json.loads(path.read_text())["invoice_id"] == invoice_id
    ]
    successful_attempts = [attempt for attempt in attempts if attempt["status"] == "succeeded"]
    policies = json.loads((dataset_root / "policies/index.json").read_text())
    policy = next(item for item in policies if item["policy_key"] == "billing_duplicate_credit")

    assert invoice["account_id"] == crm_account["account_id"]
    assert invoice["subscription_id"] == subscription["subscription_id"]
    assert subscription["previous_plan"] != subscription["plan"]
    assert subscription["upgraded_at"] is not None
    assert invoice["issued_at"] >= subscription["upgraded_at"]
    assert len(successful_attempts) == 2
    assert {attempt["amount_cents"] for attempt in successful_attempts} == {invoice["amount_cents"]}
    assert policy["action_type"] == "apply_account_credit"
    assert policy["approval_required"] is True
    assert policy["maximum_amount_cents"] == 10_000
    assert policy["maximum_amount_cents"] >= invoice["amount_cents"]
    assert truth["proposed_action"] == {
        "type": "apply_account_credit",
        "target_reference": duplicate_case["customer_reference"],
        "amount_cents": invoice["amount_cents"],
    }


def test_manifest_records_expected_counts_and_verifiable_hashes(
    generated_roots: tuple[Path, Path],
) -> None:
    dataset_root = generated_roots[0] / "synthetic/v1"
    manifest = DatasetManifest.model_validate_json(
        (dataset_root / "manifest.json").read_text(encoding="utf-8")
    )

    assert manifest.dataset_version == "v1"
    assert manifest.seed == 20260722
    assert manifest.generated_at.isoformat() == "2026-07-22T00:00:00+00:00"
    assert manifest.entity_counts == {
        "crm_accounts": 60,
        "crm_users": 180,
        "subscriptions": 60,
        "invoices": 600,
        "payment_attempts": 720,
        "telemetry_events": 4_800,
        "incidents": 15,
        "knowledge_base_articles": 30,
        "policies": 10,
        "support_cases": 80,
        "curated_public_cases": 10,
        "ground_truth_cases": 80,
        "replay_events": 33,
    }
    assert manifest.file_hashes
    actual_hashed_files = {
        path.relative_to(dataset_root).as_posix()
        for path in dataset_root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    assert set(manifest.file_hashes) == actual_hashed_files
    for relative_path, expected_hash in manifest.file_hashes.items():
        assert (
            hashlib.sha256((dataset_root / relative_path).read_bytes()).hexdigest() == expected_hash
        )


def test_dataset_rejects_unsafe_shapes_and_broken_references() -> None:
    with pytest.raises(ValidationError):
        CrmUser.model_validate(
            {
                "user_id": "not-a-uuid",
                "account_id": "also-not-a-uuid",
                "name": "Synthetic User",
                "email": "synthetic@example.com",
                "role": "owner",
            }
        )
    with pytest.raises(ValidationError):
        CrmUser.model_validate(
            {
                "user_id": "e946dfcb-a019-5ba4-8bf6-770e332fa7e5",
                "account_id": "a978bfcf-a0b9-531b-a99b-98c725affb48",
                "name": "Synthetic User",
                "email": "@example.com",
                "role": "owner",
            }
        )

    dataset = build_dataset()
    dataset.crm_users[0] = dataset.crm_users[0].model_copy(
        update={"account_id": UUID("ffffffff-ffff-5fff-8fff-ffffffffffff")}
    )

    with pytest.raises(ValueError, match="unknown account"):
        validate_dataset(dataset)

    unsafe_email_dataset = build_dataset()
    unsafe_email_dataset.crm_users[0] = unsafe_email_dataset.crm_users[0].model_copy(
        update={"email": "synthetic@real-customer.test"}
    )
    with pytest.raises(ValueError, match="reserved example domain"):
        validate_dataset(unsafe_email_dataset)

    broken_truth_dataset = build_dataset()
    broken_truth_dataset.ground_truth[0] = broken_truth_dataset.ground_truth[0].model_copy(
        update={"expected_evidence_ids": ["invoice:missing"]}
    )
    with pytest.raises(ValueError, match="unknown evidence"):
        validate_dataset(broken_truth_dataset)

    wrong_owner_dataset = build_dataset()
    wrong_owner_dataset.ground_truth[1] = wrong_owner_dataset.ground_truth[1].model_copy(
        update={"expected_evidence_ids": [f"crm:{wrong_owner_dataset.crm_accounts[2].account_id}"]}
    )
    with pytest.raises(ValueError, match="another account"):
        validate_dataset(wrong_owner_dataset)

    mismatched_public_approval_dataset = build_dataset()
    mismatched_public_approval_dataset.public_cases[0] = (
        mismatched_public_approval_dataset.public_cases[0].model_copy(
            update={
                "expected_approval_required": not mismatched_public_approval_dataset.public_cases[
                    0
                ].expected_approval_required
            }
        )
    )
    with pytest.raises(ValueError, match="approval expectation"):
        validate_dataset(mismatched_public_approval_dataset)

    mismatched_invoice_dataset = build_dataset()
    mismatched_invoice_dataset.ground_truth[1] = mismatched_invoice_dataset.ground_truth[
        1
    ].model_copy(update={"expected_invoice_id": mismatched_invoice_dataset.invoices[11].invoice_id})
    with pytest.raises(ValueError, match="expected invoice is not cited"):
        validate_dataset(mismatched_invoice_dataset)

    non_v5_dataset = build_dataset()
    non_v5_dataset.crm_accounts[0] = non_v5_dataset.crm_accounts[0].model_copy(
        update={"account_id": uuid4()}
    )
    with pytest.raises(ValueError, match="UUIDv5"):
        validate_dataset(non_v5_dataset)

    duplicate_policy_key_dataset = build_dataset()
    duplicate_policy_key_dataset.policies[1] = duplicate_policy_key_dataset.policies[1].model_copy(
        update={"policy_key": duplicate_policy_key_dataset.policies[0].policy_key}
    )
    with pytest.raises(ValueError, match="policy key"):
        validate_dataset(duplicate_policy_key_dataset)

    missing_duplicate_evidence_dataset = build_dataset()
    duplicate_truth = missing_duplicate_evidence_dataset.ground_truth[0]
    missing_duplicate_evidence_dataset.ground_truth[0] = duplicate_truth.model_copy(
        update={
            "expected_evidence_ids": [
                evidence_id
                for evidence_id in duplicate_truth.expected_evidence_ids
                if not evidence_id.startswith("policy:")
            ]
        }
    )
    with pytest.raises(ValueError, match="missing required evidence"):
        validate_dataset(missing_duplicate_evidence_dataset)

    missing_payment_evidence_dataset = build_dataset()
    duplicate_truth = missing_payment_evidence_dataset.ground_truth[0]
    payment_evidence = [
        evidence_id
        for evidence_id in duplicate_truth.expected_evidence_ids
        if evidence_id.startswith("payment_attempt:")
    ]
    missing_payment_evidence_dataset.ground_truth[0] = duplicate_truth.model_copy(
        update={
            "expected_evidence_ids": [
                evidence_id
                for evidence_id in duplicate_truth.expected_evidence_ids
                if evidence_id != payment_evidence[-1]
            ]
        }
    )
    with pytest.raises(ValueError, match="missing required evidence"):
        validate_dataset(missing_payment_evidence_dataset)


def test_all_generated_emails_use_reserved_example_domains() -> None:
    dataset = build_dataset()
    emails = [account.primary_email for account in dataset.crm_accounts]
    emails.extend(user.email for user in dataset.crm_users)

    assert {email.rsplit("@", 1)[1] for email in emails} <= {
        "example.com",
        "example.org",
        "example.net",
    }
    identifiers = [account.account_id for account in dataset.crm_accounts]
    identifiers.extend(case.case_id for case in dataset.public_cases)
    assert {identifier.version for identifier in identifiers} == {5}


def test_later_categories_and_adversarial_cases_have_evidence_backed_templates() -> None:
    dataset = build_dataset()
    truth_by_case = {truth.case_id: truth for truth in dataset.ground_truth}

    representative_truth: dict[CaseCategory, GroundTruth] = {}
    for case in dataset.public_cases:
        representative_truth.setdefault(case.category, truth_by_case[case.case_id])

    assert {tool.value for tool in representative_truth[CaseCategory.BILLING].required_tools} >= {
        "get_subscription",
        "list_invoices",
        "get_payment_attempts",
        "get_policy",
    }
    assert {tool.value for tool in representative_truth[CaseCategory.INCIDENT].required_tools} >= {
        "list_service_incidents",
        "get_policy",
    }
    assert {
        tool.value for tool in representative_truth[CaseCategory.PRODUCT_ISSUE].required_tools
    } >= {
        "get_product_events",
        "search_knowledge_base",
    }
    assert {
        tool.value for tool in representative_truth[CaseCategory.PLAN_LIMIT].required_tools
    } >= {
        "get_subscription",
        "get_policy",
    }
    adversarial_truth = [
        truth_by_case[case.case_id]
        for case in dataset.public_cases
        if case.split is Split.ADVERSARIAL
    ]
    assert {truth.fault_profile for truth in adversarial_truth} == {
        "prompt_injection",
        "missing_data",
        "conflicting_data",
        "tool_timeout",
        "malformed_tool",
        "policy_trap",
    }

    telemetry_by_id = {event.event_id: event for event in dataset.telemetry_events}
    articles_by_id = {article.article_id: article for article in dataset.knowledge_base_articles}
    product_truths = [
        truth_by_case[case.case_id]
        for case in dataset.public_cases
        if case.category is CaseCategory.PRODUCT_ISSUE
    ]
    for truth in product_truths:
        cited_events = [
            telemetry_by_id[UUID(evidence_id.removeprefix("telemetry:"))]
            for evidence_id in truth.expected_evidence_ids
            if evidence_id.startswith("telemetry:")
        ]
        assert len(cited_events) == 2
        assert {event.event_type for event in cited_events} == {"workflow.step_failed"}
        cited_articles = [
            articles_by_id[UUID(evidence_id.removeprefix("kb:"))]
            for evidence_id in truth.expected_evidence_ids
            if evidence_id.startswith("kb:")
        ]
        assert cited_articles
        assert {article.product_area for article in cited_articles} == {"workflows"}

    billing_truths = [
        truth_by_case[case.case_id]
        for case in dataset.public_cases
        if case.category is CaseCategory.BILLING
    ]
    assert {truth.resolution_code for truth in billing_truths} == {
        "failed_payment_confirmed",
        "post_cancellation_invoice_reviewed",
    }
    invoices_by_id = {invoice.invoice_id: invoice for invoice in dataset.invoices}
    subscriptions_by_account = {
        subscription.account_id: subscription for subscription in dataset.subscriptions
    }
    for truth in billing_truths:
        assert truth.expected_invoice_id is not None
        invoice = invoices_by_id[truth.expected_invoice_id]
        if truth.resolution_code == "failed_payment_confirmed":
            assert invoice.status == "open"
        else:
            subscription = subscriptions_by_account[invoice.account_id]
            assert subscription.status == "canceled"
            assert subscription.canceled_at is not None
            assert invoice.issued_at > subscription.canceled_at

    access_user_ids = {
        UUID(evidence_id.removeprefix("crm_user:"))
        for truth in representative_truth.values()
        for evidence_id in truth.expected_evidence_ids
        if evidence_id.startswith("crm_user:")
    }
    users_by_id = {user.user_id: user for user in dataset.crm_users}
    assert all(users_by_id[user_id].previous_role for user_id in access_user_ids)

    plan_truth = representative_truth[CaseCategory.PLAN_LIMIT]
    plan_subscription_id = UUID(
        next(
            evidence_id.removeprefix("subscription:")
            for evidence_id in plan_truth.expected_evidence_ids
            if evidence_id.startswith("subscription:")
        )
    )
    plan_subscription = next(
        item for item in dataset.subscriptions if item.subscription_id == plan_subscription_id
    )
    assert plan_subscription.usage_units <= plan_subscription.plan_limit_units
    assert plan_subscription.usage_units >= plan_subscription.plan_limit_units * 0.9


def test_public_replays_use_the_shared_workflow_event_contract(
    generated_roots: tuple[Path, Path],
) -> None:
    dataset_root = generated_roots[0] / "synthetic/v1"
    replay_paths = sorted((dataset_root / "replays").glob("*/events.jsonl"))

    assert len(replay_paths) == 10
    for replay_path in replay_paths:
        events = [
            WorkflowEvent.model_validate_json(line)
            for line in replay_path.read_text(encoding="utf-8").splitlines()
        ]
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert {event.public_payload["case_id"] for event in events} == {replay_path.parent.name}
        assert events[-1].event_type is WorkflowEventType.RUN_COMPLETED
        event_types = [event.event_type for event in events]
        if WorkflowEventType.APPROVAL_REQUESTED in event_types:
            assert (
                event_types.index(WorkflowEventType.APPROVAL_REQUESTED)
                < event_types.index(WorkflowEventType.APPROVAL_DECIDED)
                < event_types.index(WorkflowEventType.ACTION_EXECUTED)
            )


def test_repository_generator_command_writes_validated_v1_fixtures(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            sys.executable,
            str(repository_root / "scripts/generate_synthetic_data.py"),
            "--output-root",
            str(tmp_path),
        ],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "seed 20260722" in result.stdout
    assert (tmp_path / "synthetic/v1/cases/public").is_dir()
    assert (tmp_path / "synthetic/v1/cases/ground-truth").is_dir()
    assert (tmp_path / "synthetic/v1/replays").is_dir()
