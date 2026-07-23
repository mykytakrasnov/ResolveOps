import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "vitest";

import { FileObjectStorage } from "../../dev/filesystem-storage.ts";
import {
  createSyntheticApi,
  InMemoryNonceStore,
  MemoryObjectStorage,
  signServiceRequest,
} from "./index.ts";

const NOW = new Date("2026-07-22T12:00:00.000Z");
const SECRET = "test-only-service-secret";
const ACCOUNT_ID = "11111111-1111-5111-8111-111111111111";
const OTHER_ACCOUNT_ID = "22222222-2222-5222-8222-222222222222";
const CASE_ID = "33333333-3333-5333-8333-333333333333";
const INVOICE_ID = "44444444-4444-5444-8444-444444444444";

function fixtureObjects() {
  return {
    "synthetic/v1/manifest.json": JSON.stringify({
      dataset_version: "v1",
      generated_at: "2026-07-22T00:00:00Z",
      entity_counts: { crm_accounts: 1, invoices: 1 },
    }),
    [`synthetic/v1/cases/public/${CASE_ID}.json`]: JSON.stringify({
      case_id: CASE_ID,
      split: "development",
      category: "duplicate_charge",
      difficulty: "medium",
      curated: true,
      expected_approval_required: true,
      subject: "Charged twice",
      body: "Two synthetic charges appear for one period.",
      customer_reference: "org_atlas_001",
      created_at: "2026-07-22T11:00:00Z",
      attachments: [],
      resolution_code: "must_be_redacted_even_if_misfiled",
      hidden_truth: { approval_required: true },
    }),
    [`synthetic/v1/cases/ground-truth/${CASE_ID}.yaml`]: JSON.stringify({
      resolution_code: "must_never_be_returned",
      expected_evidence_ids: ["secret"],
    }),
    [`synthetic/v1/replays/${CASE_ID}/events.jsonl`]: `${JSON.stringify({
      event_id: 1,
      run_id: "88888888-8888-5888-8888-888888888888",
      sequence: 1,
      event_type: "run.completed",
      node_name: null,
      status: "completed",
      public_payload: {
        case_id: CASE_ID,
        summary: "Synthetic investigation completed.",
      },
      payload_hash: "a".repeat(64),
      created_at: "2026-07-22T12:00:00Z",
      langfuse_trace_id: "must-never-cross-the-public-boundary",
    })}\n`,
    [`synthetic/v1/crm/accounts/${ACCOUNT_ID}.json`]: JSON.stringify({
      account_id: ACCOUNT_ID,
      customer_reference: "org_atlas_001",
      name: "Example Industries",
      primary_email: "billing@example.com",
      region: "us-east",
      status: "active",
      created_at: "2025-01-01T00:00:00Z",
    }),
    [`synthetic/v1/billing/accounts/${ACCOUNT_ID}.json`]: JSON.stringify({
      subscription_id: "55555555-5555-5555-8555-555555555555",
      account_id: ACCOUNT_ID,
      plan: "starter",
      status: "active",
      amount_cents: 4900,
      currency: "USD",
      current_period_start: "2026-07-01",
      current_period_end: "2026-08-01",
      plan_limit_units: 1000,
      usage_units: 850,
      previous_plan: null,
      upgraded_at: null,
      canceled_at: null,
    }),
    [`synthetic/v1/billing/invoices/${INVOICE_ID}.json`]: JSON.stringify({
      invoice_id: INVOICE_ID,
      account_id: ACCOUNT_ID,
      subscription_id: "55555555-5555-5555-8555-555555555555",
      period_start: "2026-07-01",
      period_end: "2026-08-01",
      amount_cents: 4900,
      currency: "USD",
      status: "paid",
      issued_at: "2026-07-21T00:00:00Z",
    }),
    "synthetic/v1/billing/payment-attempts/66666666-6666-5666-8666-666666666666.json":
      JSON.stringify({
        payment_attempt_id: "66666666-6666-5666-8666-666666666666",
        account_id: ACCOUNT_ID,
        invoice_id: INVOICE_ID,
        amount_cents: 4900,
        currency: "USD",
        status: "succeeded",
        processor_reference: "pay_example_0001",
        attempted_at: "2026-07-22T00:00:00Z",
      }),
    "synthetic/v1/policies/index.json": JSON.stringify([
      {
        policy_id: "77777777-7777-5777-8777-777777777777",
        policy_key: "billing_duplicate_credit",
        version: "3.0",
        action_type: "apply_account_credit",
        maximum_amount_cents: 10000,
        approval_required: true,
        effective_at: "2026-01-01T00:00:00Z",
        body: "A synthetic duplicate charge may receive a credit after approval.",
      },
    ]),
  };
}

function createFixtureApi() {
  return createSyntheticApi({
    storage: new MemoryObjectStorage(fixtureObjects()),
    hmacSecret: SECRET,
    nonceStore: new InMemoryNonceStore(),
    now: () => NOW,
  });
}

async function signedRequest(
  path,
  {
    method = "GET",
    nonce = crypto.randomUUID(),
    now = NOW,
    accountId = accountIdFromPath(path),
  } = {},
) {
  const timestamp = Math.floor(now.getTime() / 1000).toString();
  const signature = await signServiceRequest({
    secret: SECRET,
    method,
    pathAndQuery: path,
    timestamp,
    nonce,
    accountId,
  });
  const headers = {
    "X-Service-Timestamp": timestamp,
    "X-Service-Nonce": nonce,
    "X-Service-Signature": signature,
  };
  if (accountId !== undefined) {
    headers["X-Service-Account-ID"] = accountId;
  }
  return new Request(`https://resolveops.example${path}`, {
    method,
    headers,
  });
}

function accountIdFromPath(path) {
  const accountPath = path.match(/\/accounts\/([0-9a-f-]{36})(?:\/|\?|$)/)?.[1];
  if (accountPath !== undefined) {
    return accountPath;
  }
  return (
    new URL(`https://resolveops.example${path}`).searchParams.get(
      "account_id",
    ) ?? undefined
  );
}

test("public routes list curated cases and return a redacted case", async () => {
  const api = createFixtureApi();

  const listResponse = await api.fetch(
    new Request("https://resolveops.example/api/v1/cases?limit=10"),
  );
  assert.equal(listResponse.status, 200);
  const list = await listResponse.json();
  assert.equal(list.items.length, 1);
  assert.equal(list.items[0].case_id, CASE_ID);
  assert.equal(list.items[0].expected_approval_required, true);
  assert.equal(list.items[0].resolution_code, undefined);
  assert.equal(list.items[0].expected_evidence_ids, undefined);
  assert.equal(list.items[0].hidden_truth, undefined);

  const detailResponse = await api.fetch(
    new Request(`https://resolveops.example/api/v1/cases/${CASE_ID}`),
  );
  assert.equal(detailResponse.status, 200);
  const detail = await detailResponse.json();
  assert.equal(detail.case_id, CASE_ID);
  assert.equal(detail.expected_approval_required, true);
  assert.equal(detail.resolution_code, undefined);
  assert.equal(detail.hidden_truth, undefined);
});

test("public replay routes use static sanitized artifacts without service authentication", async () => {
  const api = createFixtureApi();
  const listResponse = await api.fetch(
    new Request("https://resolveops.example/api/v1/public/replays?limit=10"),
  );
  assert.equal(listResponse.status, 200);
  const list = await listResponse.json();
  assert.deepEqual(
    list.items.map((item) => item.case_id),
    [CASE_ID],
  );

  const detailResponse = await api.fetch(
    new Request(`https://resolveops.example/api/v1/public/replays/${CASE_ID}`),
  );
  assert.equal(detailResponse.status, 200);
  const detail = await detailResponse.json();
  assert.equal(detail.case.case_id, CASE_ID);
  assert.equal(detail.events[0].event_type, "run.completed");
  assert.deepEqual(Object.keys(detail.events[0].public_payload).sort(), [
    "case_id",
    "summary",
  ]);
  assert.equal(detail.events[0].langfuse_trace_id, undefined);
});

test("public replay routes reject forbidden fields inside the public payload", async () => {
  const objects = fixtureObjects();
  const replayKey = `synthetic/v1/replays/${CASE_ID}/events.jsonl`;
  const event = JSON.parse(objects[replayKey]);
  event.public_payload.internal_trace_id =
    "must-never-cross-the-public-boundary";
  objects[replayKey] = `${JSON.stringify(event)}\n`;
  const api = createSyntheticApi({
    storage: new MemoryObjectStorage(objects),
    hmacSecret: SECRET,
    nonceStore: new InMemoryNonceStore(),
    now: () => NOW,
  });

  const response = await api.fetch(
    new Request(`https://resolveops.example/api/v1/public/replays/${CASE_ID}`),
  );

  assert.equal(response.status, 500);
});

test("local filesystem storage serves generated synthetic case routes", async () => {
  const fixtureRoot = await mkdtemp(
    path.join(tmpdir(), "resolveops-synthetic-api-"),
  );
  try {
    const objects = fixtureObjects();
    await Promise.all(
      Object.entries(objects).map(async ([key, value]) => {
        const destination = path.join(fixtureRoot, key);
        await mkdir(path.dirname(destination), { recursive: true });
        await writeFile(destination, value, "utf8");
      }),
    );
    const api = createSyntheticApi({
      storage: new FileObjectStorage(fixtureRoot),
      hmacSecret: SECRET,
      nonceStore: new InMemoryNonceStore(),
      now: () => NOW,
    });

    const response = await api.fetch(
      new Request("http://resolveops.local/api/v1/cases?limit=10"),
    );

    assert.equal(response.status, 200);
    assert.deepEqual(
      (await response.json()).items.map((item) => item.case_id),
      [CASE_ID],
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("service routes return typed CRM, billing, payment, policy, and status data", async () => {
  const api = createFixtureApi();
  const paths = [
    "/systems/v1/crm/accounts?customer_reference=org_atlas_001",
    `/systems/v1/crm/accounts/${ACCOUNT_ID}`,
    `/systems/v1/billing/accounts/${ACCOUNT_ID}/subscription`,
    `/systems/v1/billing/accounts/${ACCOUNT_ID}/invoices?from=2026-07-01&to=2026-07-31&limit=25`,
    `/systems/v1/billing/invoices/${INVOICE_ID}/payment-attempts?account_id=${ACCOUNT_ID}&limit=25`,
    "/systems/v1/policies/billing_duplicate_credit?version=3.0",
    "/systems/v1/status",
  ];

  const responses = await Promise.all(
    paths.map(async (path) => api.fetch(await signedRequest(path))),
  );
  assert.deepEqual(
    responses.map((response) => response.status),
    [200, 200, 200, 200, 200, 200, 200],
  );

  const lookup = await responses[0].json();
  const account = await responses[1].json();
  const invoices = await responses[3].json();
  const attempts = await responses[4].json();
  const policy = await responses[5].json();
  const status = await responses[6].json();
  assert.equal(lookup.account_id, ACCOUNT_ID);
  assert.equal(account.account_id, ACCOUNT_ID);
  assert.equal(invoices.items[0].invoice_id, INVOICE_ID);
  assert.equal(attempts.items[0].account_id, ACCOUNT_ID);
  assert.equal(policy.policy_key, "billing_duplicate_credit");
  assert.equal(status.dataset_version, "v1");
});

test("missing and wrong-owner objects return the same non-leaking 404 contract", async () => {
  const api = createFixtureApi();
  const missing = `/systems/v1/crm/accounts/99999999-9999-5999-8999-999999999999`;
  const wrongOwner = `/systems/v1/billing/invoices/${INVOICE_ID}/payment-attempts?account_id=${OTHER_ACCOUNT_ID}&limit=25`;

  for (const path of [missing, wrongOwner]) {
    const response = await api.fetch(await signedRequest(path));
    assert.equal(response.status, 404);
    assert.deepEqual(await response.json(), {
      error: { code: "not_found", message: "Synthetic object was not found." },
    });
  }

  const wrongScopedAccount = `/systems/v1/crm/accounts/${ACCOUNT_ID}`;
  const response = await api.fetch(
    await signedRequest(wrongScopedAccount, { accountId: OTHER_ACCOUNT_ID }),
  );
  assert.equal(response.status, 404);
  assert.equal((await response.json()).error.code, "not_found");
});

test("service authentication rejects invalid signatures and stale timestamps", async () => {
  const api = createFixtureApi();
  const path = `/systems/v1/crm/accounts/${ACCOUNT_ID}`;
  const invalid = await signedRequest(path);
  invalid.headers.set("X-Service-Signature", "0".repeat(64));
  assert.equal((await api.fetch(invalid)).status, 401);

  const stale = await signedRequest(path, {
    now: new Date(NOW.getTime() - 301_000),
  });
  assert.equal((await api.fetch(stale)).status, 401);

  const missingOwner = await signedRequest(path, { accountId: "" });
  assert.equal((await api.fetch(missingOwner)).status, 401);

  const tamperedOwner = await signedRequest(path);
  tamperedOwner.headers.set("X-Service-Account-ID", OTHER_ACCOUNT_ID);
  assert.equal((await api.fetch(tamperedOwner)).status, 401);
});

test("a future-dated nonce remains claimed for its full signature validity window", async () => {
  let clock = NOW;
  const api = createSyntheticApi({
    storage: new MemoryObjectStorage(fixtureObjects()),
    hmacSecret: SECRET,
    nonceStore: new InMemoryNonceStore(),
    now: () => clock,
  });
  const path = `/systems/v1/crm/accounts/${ACCOUNT_ID}`;
  const future = new Date(NOW.getTime() + 300_000);
  const request = await signedRequest(path, {
    nonce: "future-window-nonce",
    now: future,
  });
  assert.equal((await api.fetch(request)).status, 200);

  clock = new Date(NOW.getTime() + 301_000);
  const replay = new Request(request.url, { headers: request.headers });
  assert.equal((await api.fetch(replay)).status, 409);
});

test("signed method and query are immutable and nonces cannot be replayed", async () => {
  const api = createFixtureApi();
  const path = `/systems/v1/crm/accounts/${ACCOUNT_ID}`;
  const nonce = "nonce-replay-test";
  const request = await signedRequest(path, { nonce });
  assert.equal((await api.fetch(request)).status, 200);
  assert.equal(
    (await api.fetch(await signedRequest(path, { nonce }))).status,
    409,
  );

  const signed = await signedRequest(path);
  const tampered = new Request(`${signed.url}?extra=true`, signed);
  assert.equal((await api.fetch(tampered)).status, 401);

  const signedGet = await signedRequest(path);
  const tamperedMethod = new Request(signedGet.url, {
    method: "POST",
    headers: signedGet.headers,
  });
  assert.equal((await api.fetch(tamperedMethod)).status, 401);

  const post = await signedRequest(path, { method: "POST" });
  assert.equal((await api.fetch(post)).status, 405);
  const head = await signedRequest(path, { method: "HEAD" });
  assert.equal((await api.fetch(head)).status, 405);
});

test("unbounded or malformed query parameters return 422", async () => {
  const api = createFixtureApi();
  const paths = [
    `/systems/v1/billing/accounts/${ACCOUNT_ID}/invoices`,
    `/systems/v1/billing/accounts/${ACCOUNT_ID}/invoices?from=2024-01-01&to=2026-07-31&limit=25`,
    `/systems/v1/billing/accounts/${ACCOUNT_ID}/invoices?from=2026-07-01&to=2026-07-31&limit=101`,
    `/systems/v1/billing/invoices/${INVOICE_ID}/payment-attempts?account_id=${ACCOUNT_ID}&limit=0`,
  ];

  for (const path of paths) {
    const response = await api.fetch(await signedRequest(path));
    assert.equal(response.status, 422);
    assert.equal((await response.json()).error.code, "invalid_query");
  }
});

test("malformed fixture objects fail the typed boundary instead of being returned", async () => {
  const objects = fixtureObjects();
  const key = `synthetic/v1/billing/accounts/${ACCOUNT_ID}.json`;
  const malformed = JSON.parse(objects[key]);
  delete malformed.canceled_at;
  objects[key] = JSON.stringify(malformed);
  const api = createSyntheticApi({
    storage: new MemoryObjectStorage(objects),
    hmacSecret: SECRET,
    nonceStore: new InMemoryNonceStore(),
    now: () => NOW,
  });

  const path = `/systems/v1/billing/accounts/${ACCOUNT_ID}/subscription`;
  const response = await api.fetch(await signedRequest(path));
  assert.equal(response.status, 500);
  assert.deepEqual(await response.json(), {
    error: {
      code: "fixture_unavailable",
      message: "Synthetic data is unavailable.",
    },
  });
});

test("ground-truth and arbitrary object paths are unreachable from both route groups", async () => {
  const api = createFixtureApi();
  const publicTraversal = `/api/v1/cases/..%2Fground-truth%2F${CASE_ID}`;
  assert.equal(
    (
      await api.fetch(
        new Request(`https://resolveops.example${publicTraversal}`),
      )
    ).status,
    404,
  );

  const serviceGroundTruth = `/systems/v1/cases/ground-truth/${CASE_ID}`;
  assert.equal(
    (await api.fetch(await signedRequest(serviceGroundTruth))).status,
    404,
  );
});
