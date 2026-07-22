import { Hono } from "hono";

import {
  authenticateServiceRequest,
  InMemoryNonceStore,
  type NonceStore,
  signServiceRequest,
} from "./hmac.ts";
import {
  MemoryObjectStorage,
  type ObjectStorage,
  R2ObjectStorage,
} from "./storage.ts";
import type {
  CrmAccount,
  Invoice,
  Page,
  PaymentAttempt,
  PolicyDocument,
  PublicCase,
  Subscription,
  SyntheticStatus,
} from "./types.ts";

export {
  InMemoryNonceStore,
  MemoryObjectStorage,
  R2ObjectStorage,
  signServiceRequest,
};
export type { NonceStore, ObjectStorage };
export type {
  CrmAccount,
  Invoice,
  Page,
  PaymentAttempt,
  PolicyDocument,
  PublicCase,
  Subscription,
  SyntheticStatus,
} from "./types.ts";

const DATASET_PREFIX = "synthetic/v1/";
const PUBLIC_CASE_PREFIX = `${DATASET_PREFIX}cases/public/`;
const CRM_ACCOUNT_PREFIX = `${DATASET_PREFIX}crm/accounts/`;
const BILLING_ACCOUNT_PREFIX = `${DATASET_PREFIX}billing/accounts/`;
const INVOICE_PREFIX = `${DATASET_PREFIX}billing/invoices/`;
const PAYMENT_ATTEMPT_PREFIX = `${DATASET_PREFIX}billing/payment-attempts/`;
const POLICY_INDEX_KEY = `${DATASET_PREFIX}policies/index.json`;
const MANIFEST_KEY = `${DATASET_PREFIX}manifest.json`;
const UUID_PATTERN =
  "[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}";
const MAX_STORAGE_KEYS = 1_000;

class HttpError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function jsonResponse(
  value: unknown,
  status = 200,
  headers?: HeadersInit,
): Response {
  return Response.json(value, {
    status,
    headers: { "Cache-Control": "no-store", ...headers },
  });
}

function notFound(): never {
  throw new HttpError(404, "not_found", "Synthetic object was not found.");
}

function invalidQuery(message: string): never {
  throw new HttpError(422, "invalid_query", message);
}

function methodNotAllowed(): Response {
  return jsonResponse(
    {
      error: {
        code: "method_not_allowed",
        message: "Only GET is supported.",
      },
    },
    405,
    { Allow: "GET" },
  );
}

function requireAccountOwnership(
  request: Request,
  expectedAccountId: string,
): void {
  const accountId = request.headers.get("X-Service-Account-ID");
  if (accountId === null || !new RegExp(`^${UUID_PATTERN}$`).test(accountId)) {
    throw new HttpError(
      401,
      "invalid_service_owner",
      "Service ownership context is required.",
    );
  }
  if (accountId !== expectedAccountId) {
    notFound();
  }
}

function parseJsonObject(text: string, label: string): Record<string, unknown> {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error(`Invalid ${label} fixture JSON.`);
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`Invalid ${label} fixture shape.`);
  }
  return value as Record<string, unknown>;
}

function parseJsonArray(text: string, label: string): readonly unknown[] {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error(`Invalid ${label} fixture JSON.`);
  }
  if (!Array.isArray(value)) {
    throw new Error(`Invalid ${label} fixture shape.`);
  }
  return value;
}

function requireProperties(
  object: Record<string, unknown>,
  label: string,
  properties: Readonly<
    Record<string, "string" | "number" | "boolean" | "object">
  >,
): void {
  for (const [property, expectedType] of Object.entries(properties)) {
    const value = object[property];
    if (
      typeof value !== expectedType ||
      (expectedType === "object" && value === null)
    ) {
      throw new Error(`Invalid ${label} fixture property: ${property}.`);
    }
  }
}

function requireUuid(
  object: Record<string, unknown>,
  property: string,
  label: string,
): void {
  if (
    typeof object[property] !== "string" ||
    !new RegExp(`^${UUID_PATTERN}$`).test(object[property])
  ) {
    throw new Error(`Invalid ${label} fixture UUID: ${property}.`);
  }
}

function requireIsoDate(
  object: Record<string, unknown>,
  property: string,
  label: string,
  dateOnly = false,
): void {
  const value = object[property];
  if (typeof value !== "string") {
    throw new Error(`Invalid ${label} fixture date: ${property}.`);
  }
  const pattern = dateOnly
    ? /^\d{4}-\d{2}-\d{2}$/
    : /^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/;
  const parsed = Date.parse(dateOnly ? `${value}T00:00:00.000Z` : value);
  if (
    !pattern.test(value) ||
    Number.isNaN(parsed) ||
    (dateOnly && new Date(parsed).toISOString().slice(0, 10) !== value)
  ) {
    throw new Error(`Invalid ${label} fixture date: ${property}.`);
  }
}

function requireInteger(
  object: Record<string, unknown>,
  property: string,
  label: string,
  minimum: number,
): void {
  const value = object[property];
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < minimum
  ) {
    throw new Error(`Invalid ${label} fixture integer: ${property}.`);
  }
}

function requireNullableString(
  object: Record<string, unknown>,
  property: string,
  label: string,
): void {
  if (
    !(property in object) ||
    (object[property] !== null && typeof object[property] !== "string")
  ) {
    throw new Error(`Invalid ${label} fixture nullable property: ${property}.`);
  }
}

function parsePublicCase(text: string): PublicCase {
  const object = parseJsonObject(text, "public case");
  requireProperties(object, "public case", {
    case_id: "string",
    split: "string",
    category: "string",
    difficulty: "string",
    curated: "boolean",
    subject: "string",
    body: "string",
    customer_reference: "string",
    created_at: "string",
    attachments: "object",
  });
  if (!Array.isArray(object.attachments)) {
    throw new Error("Invalid public case fixture property: attachments.");
  }
  requireUuid(object, "case_id", "public case");
  requireIsoDate(object, "created_at", "public case");
  if (!/^org_atlas_\d{3}$/.test(object.customer_reference as string)) {
    throw new Error("Invalid public case fixture customer_reference.");
  }
  const attachments = object.attachments.map((attachment) => {
    if (
      typeof attachment !== "object" ||
      attachment === null ||
      Array.isArray(attachment)
    ) {
      throw new Error("Invalid public case attachment fixture shape.");
    }
    const entries = Object.entries(attachment);
    if (entries.some(([, value]) => typeof value !== "string")) {
      throw new Error("Invalid public case attachment fixture property.");
    }
    return Object.fromEntries(entries) as Record<string, string>;
  });
  // Build the response explicitly so misplaced evaluation fields cannot cross this boundary.
  return {
    case_id: object.case_id as string,
    split: object.split as string,
    category: object.category as string,
    difficulty: object.difficulty as string,
    curated: object.curated as boolean,
    subject: object.subject as string,
    body: object.body as string,
    customer_reference: object.customer_reference as string,
    created_at: object.created_at as string,
    attachments,
  };
}

function parseCrmAccount(text: string): CrmAccount {
  const object = parseJsonObject(text, "CRM account");
  requireProperties(object, "CRM account", {
    account_id: "string",
    customer_reference: "string",
    name: "string",
    primary_email: "string",
    region: "string",
    status: "string",
    created_at: "string",
  });
  requireUuid(object, "account_id", "CRM account");
  requireIsoDate(object, "created_at", "CRM account");
  if (!/^org_atlas_\d{3}$/.test(object.customer_reference as string)) {
    throw new Error("Invalid CRM account fixture customer_reference.");
  }
  if (
    !/^[A-Za-z0-9][A-Za-z0-9._+-]*@(example\.com|example\.org|example\.net)$/.test(
      object.primary_email as string,
    )
  ) {
    throw new Error("Invalid CRM account fixture email domain.");
  }
  return object as unknown as CrmAccount;
}

function parseSubscription(text: string): Subscription {
  const object = parseJsonObject(text, "subscription");
  requireProperties(object, "subscription", {
    subscription_id: "string",
    account_id: "string",
    plan: "string",
    status: "string",
    amount_cents: "number",
    currency: "string",
    current_period_start: "string",
    current_period_end: "string",
    plan_limit_units: "number",
    usage_units: "number",
  });
  requireUuid(object, "subscription_id", "subscription");
  requireUuid(object, "account_id", "subscription");
  requireIsoDate(object, "current_period_start", "subscription", true);
  requireIsoDate(object, "current_period_end", "subscription", true);
  requireInteger(object, "amount_cents", "subscription", 1);
  requireInteger(object, "plan_limit_units", "subscription", 1);
  requireInteger(object, "usage_units", "subscription", 0);
  requireNullableString(object, "previous_plan", "subscription");
  requireNullableString(object, "upgraded_at", "subscription");
  requireNullableString(object, "canceled_at", "subscription");
  if (object.upgraded_at !== null) {
    requireIsoDate(object, "upgraded_at", "subscription");
  }
  if (object.canceled_at !== null) {
    requireIsoDate(object, "canceled_at", "subscription");
  }
  if (!/^[A-Z]{3}$/.test(object.currency as string)) {
    throw new Error("Invalid subscription fixture currency.");
  }
  return object as unknown as Subscription;
}

function parseInvoice(text: string): Invoice {
  const object = parseJsonObject(text, "invoice");
  requireProperties(object, "invoice", {
    invoice_id: "string",
    account_id: "string",
    subscription_id: "string",
    period_start: "string",
    period_end: "string",
    amount_cents: "number",
    currency: "string",
    status: "string",
    issued_at: "string",
  });
  requireUuid(object, "invoice_id", "invoice");
  requireUuid(object, "account_id", "invoice");
  requireUuid(object, "subscription_id", "invoice");
  requireIsoDate(object, "period_start", "invoice", true);
  requireIsoDate(object, "period_end", "invoice", true);
  requireIsoDate(object, "issued_at", "invoice");
  requireInteger(object, "amount_cents", "invoice", 1);
  if (!/^[A-Z]{3}$/.test(object.currency as string)) {
    throw new Error("Invalid invoice fixture currency.");
  }
  return object as unknown as Invoice;
}

function parsePaymentAttempt(text: string): PaymentAttempt {
  const object = parseJsonObject(text, "payment attempt");
  requireProperties(object, "payment attempt", {
    payment_attempt_id: "string",
    account_id: "string",
    invoice_id: "string",
    amount_cents: "number",
    currency: "string",
    status: "string",
    processor_reference: "string",
    attempted_at: "string",
  });
  requireUuid(object, "payment_attempt_id", "payment attempt");
  requireUuid(object, "account_id", "payment attempt");
  requireUuid(object, "invoice_id", "payment attempt");
  requireIsoDate(object, "attempted_at", "payment attempt");
  requireInteger(object, "amount_cents", "payment attempt", 1);
  if (!/^[A-Z]{3}$/.test(object.currency as string)) {
    throw new Error("Invalid payment attempt fixture currency.");
  }
  return object as unknown as PaymentAttempt;
}

function parsePolicy(value: unknown): PolicyDocument {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Invalid policy fixture shape.");
  }
  const object = value as Record<string, unknown>;
  requireProperties(object, "policy", {
    policy_id: "string",
    policy_key: "string",
    version: "string",
    action_type: "string",
    approval_required: "boolean",
    effective_at: "string",
    body: "string",
  });
  requireUuid(object, "policy_id", "policy");
  requireIsoDate(object, "effective_at", "policy");
  if (
    !/^[a-z0-9_]{1,80}$/.test(object.policy_key as string) ||
    !/^\d{1,4}\.\d{1,4}$/.test(object.version as string)
  ) {
    throw new Error("Invalid policy fixture identity.");
  }
  const maximumAmount = object.maximum_amount_cents;
  if (
    !("maximum_amount_cents" in object) ||
    (maximumAmount !== null &&
      (typeof maximumAmount !== "number" ||
        !Number.isSafeInteger(maximumAmount) ||
        maximumAmount <= 0))
  ) {
    throw new Error("Invalid policy fixture maximum_amount_cents.");
  }
  return object as unknown as PolicyDocument;
}

function parseStatus(text: string): SyntheticStatus {
  const object = parseJsonObject(text, "manifest");
  requireProperties(object, "manifest", {
    dataset_version: "string",
    generated_at: "string",
    entity_counts: "object",
  });
  requireIsoDate(object, "generated_at", "manifest");
  const entityCounts = object.entity_counts as Record<string, unknown>;
  if (
    Array.isArray(entityCounts) ||
    Object.values(entityCounts).some(
      (count) =>
        typeof count !== "number" || !Number.isSafeInteger(count) || count < 0,
    )
  ) {
    throw new Error("Invalid manifest fixture property: entity_counts.");
  }
  return {
    status: "available",
    dataset_version: object.dataset_version as string,
    generated_at: object.generated_at as string,
    entity_counts: entityCounts as Record<string, number>,
  };
}

async function readRequired(
  storage: ObjectStorage,
  key: string,
): Promise<string> {
  const value = await storage.get(key);
  if (value === null) {
    notFound();
  }
  return value;
}

function rejectUnknownParameters(
  parameters: URLSearchParams,
  allowed: readonly string[],
): void {
  const allowedSet = new Set(allowed);
  for (const key of parameters.keys()) {
    if (!allowedSet.has(key)) {
      invalidQuery(`Unsupported query parameter: ${key}.`);
    }
  }
}

function singleParameter(
  parameters: URLSearchParams,
  name: string,
): string | null {
  const values = parameters.getAll(name);
  if (values.length > 1) {
    invalidQuery(`Query parameter ${name} may appear only once.`);
  }
  return values[0] ?? null;
}

function boundedInteger(
  parameters: URLSearchParams,
  name: string,
  defaultValue: number,
  minimum: number,
  maximum: number,
): number {
  const raw = singleParameter(parameters, name);
  if (raw === null) {
    return defaultValue;
  }
  if (!/^\d+$/.test(raw)) {
    invalidQuery(`Query parameter ${name} must be an integer.`);
  }
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    invalidQuery(
      `Query parameter ${name} must be between ${minimum} and ${maximum}.`,
    );
  }
  return value;
}

function isoDate(
  parameters: URLSearchParams,
  name: string,
): { raw: string; date: Date } {
  const raw = singleParameter(parameters, name);
  if (raw === null || !/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    invalidQuery(`Query parameter ${name} must be an ISO date.`);
  }
  const date = new Date(`${raw}T00:00:00.000Z`);
  if (Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== raw) {
    invalidQuery(`Query parameter ${name} must be a valid ISO date.`);
  }
  return { raw, date };
}

function page<T>(items: readonly T[], cursor: number, limit: number): Page<T> {
  const selected = items.slice(cursor, cursor + limit);
  return {
    items: selected,
    page: {
      limit,
      next_cursor:
        cursor + selected.length < items.length
          ? String(cursor + selected.length)
          : null,
    },
  };
}

async function readObjects<T>(
  storage: ObjectStorage,
  prefix: string,
  parser: (text: string) => T,
): Promise<readonly T[]> {
  const keys = await storage.list(prefix, MAX_STORAGE_KEYS);
  return Promise.all(
    keys
      .filter((key) => key.startsWith(prefix) && key.endsWith(".json"))
      .map(async (key) => parser(await readRequired(storage, key))),
  );
}

interface ApiOptions {
  storage: ObjectStorage;
  hmacSecret: string;
  nonceStore?: NonceStore;
  now?: () => Date;
}

export function createSyntheticApi(options: ApiOptions): {
  fetch(request: Request): Promise<Response>;
} {
  const nonceStore = options.nonceStore ?? new InMemoryNonceStore();
  const now = options.now ?? (() => new Date());

  async function publicCases(url: URL): Promise<Response> {
    rejectUnknownParameters(url.searchParams, [
      "limit",
      "cursor",
      "category",
      "difficulty",
    ]);
    const limit = boundedInteger(url.searchParams, "limit", 20, 1, 50);
    const cursor = boundedInteger(
      url.searchParams,
      "cursor",
      0,
      0,
      MAX_STORAGE_KEYS,
    );
    const category = singleParameter(url.searchParams, "category");
    const difficulty = singleParameter(url.searchParams, "difficulty");
    if (category !== null && !/^[a-z_]{1,40}$/.test(category)) {
      invalidQuery("Query parameter category is invalid.");
    }
    if (difficulty !== null && !/^[a-z]{1,20}$/.test(difficulty)) {
      invalidQuery("Query parameter difficulty is invalid.");
    }
    const cases = (
      await readObjects(options.storage, PUBLIC_CASE_PREFIX, parsePublicCase)
    )
      .filter(
        (supportCase) =>
          supportCase.curated &&
          (category === null || supportCase.category === category) &&
          (difficulty === null || supportCase.difficulty === difficulty),
      )
      .sort((left, right) => right.created_at.localeCompare(left.created_at));
    return jsonResponse(page(cases, cursor, limit));
  }

  async function publicCase(caseId: string, url: URL): Promise<Response> {
    rejectUnknownParameters(url.searchParams, []);
    const value = await options.storage.get(
      `${PUBLIC_CASE_PREFIX}${caseId}.json`,
    );
    if (value === null) {
      notFound();
    }
    const supportCase = parsePublicCase(value);
    if (!supportCase.curated || supportCase.case_id !== caseId) {
      notFound();
    }
    return jsonResponse(supportCase);
  }

  async function crmAccount(
    accountId: string,
    url: URL,
    request: Request,
  ): Promise<Response> {
    rejectUnknownParameters(url.searchParams, []);
    requireAccountOwnership(request, accountId);
    const account = parseCrmAccount(
      await readRequired(
        options.storage,
        `${CRM_ACCOUNT_PREFIX}${accountId}.json`,
      ),
    );
    if (account.account_id !== accountId) {
      notFound();
    }
    return jsonResponse(account);
  }

  async function subscription(
    accountId: string,
    url: URL,
    request: Request,
  ): Promise<Response> {
    rejectUnknownParameters(url.searchParams, []);
    requireAccountOwnership(request, accountId);
    const item = parseSubscription(
      await readRequired(
        options.storage,
        `${BILLING_ACCOUNT_PREFIX}${accountId}.json`,
      ),
    );
    if (item.account_id !== accountId) {
      notFound();
    }
    return jsonResponse(item);
  }

  async function invoices(
    accountId: string,
    url: URL,
    request: Request,
  ): Promise<Response> {
    rejectUnknownParameters(url.searchParams, [
      "from",
      "to",
      "limit",
      "cursor",
    ]);
    const from = isoDate(url.searchParams, "from");
    const to = isoDate(url.searchParams, "to");
    if (
      to.date < from.date ||
      to.date.getTime() - from.date.getTime() > 366 * 86_400_000
    ) {
      invalidQuery(
        "Invoice date range must be ordered and no longer than 366 days.",
      );
    }
    const limit = boundedInteger(url.searchParams, "limit", 50, 1, 100);
    const cursor = boundedInteger(
      url.searchParams,
      "cursor",
      0,
      0,
      MAX_STORAGE_KEYS,
    );
    requireAccountOwnership(request, accountId);
    const items = (
      await readObjects(options.storage, INVOICE_PREFIX, parseInvoice)
    )
      .filter(
        (invoice) =>
          invoice.account_id === accountId &&
          invoice.issued_at.slice(0, 10) >= from.raw &&
          invoice.issued_at.slice(0, 10) <= to.raw,
      )
      .sort((left, right) => right.issued_at.localeCompare(left.issued_at));
    return jsonResponse(page(items, cursor, limit));
  }

  async function paymentAttempts(
    invoiceId: string,
    url: URL,
    request: Request,
  ): Promise<Response> {
    rejectUnknownParameters(url.searchParams, [
      "account_id",
      "limit",
      "cursor",
    ]);
    const accountId = singleParameter(url.searchParams, "account_id");
    if (
      accountId === null ||
      !new RegExp(`^${UUID_PATTERN}$`).test(accountId)
    ) {
      invalidQuery("Query parameter account_id must be a UUID.");
    }
    const limit = boundedInteger(url.searchParams, "limit", 50, 1, 100);
    const cursor = boundedInteger(
      url.searchParams,
      "cursor",
      0,
      0,
      MAX_STORAGE_KEYS,
    );
    requireAccountOwnership(request, accountId);
    const invoiceValue = await options.storage.get(
      `${INVOICE_PREFIX}${invoiceId}.json`,
    );
    if (invoiceValue === null) {
      notFound();
    }
    const invoice = parseInvoice(invoiceValue);
    if (invoice.invoice_id !== invoiceId || invoice.account_id !== accountId) {
      notFound();
    }
    const items = (
      await readObjects(
        options.storage,
        PAYMENT_ATTEMPT_PREFIX,
        parsePaymentAttempt,
      )
    )
      .filter(
        (attempt) =>
          attempt.invoice_id === invoiceId && attempt.account_id === accountId,
      )
      .sort((left, right) =>
        right.attempted_at.localeCompare(left.attempted_at),
      );
    return jsonResponse(page(items, cursor, limit));
  }

  async function policy(policyKey: string, url: URL): Promise<Response> {
    rejectUnknownParameters(url.searchParams, ["version"]);
    const version = singleParameter(url.searchParams, "version");
    if (version === null || !/^\d{1,4}\.\d{1,4}$/.test(version)) {
      invalidQuery("Query parameter version is required and must be bounded.");
    }
    const policies = parseJsonArray(
      await readRequired(options.storage, POLICY_INDEX_KEY),
      "policy index",
    ).map(parsePolicy);
    const item = policies.find(
      (candidate) =>
        candidate.policy_key === policyKey && candidate.version === version,
    );
    if (item === undefined) {
      notFound();
    }
    return jsonResponse(item);
  }

  async function status(url: URL): Promise<Response> {
    rejectUnknownParameters(url.searchParams, []);
    return jsonResponse(
      parseStatus(await readRequired(options.storage, MANIFEST_KEY)),
    );
  }

  const app = new Hono();

  app.use("*", async (context, next) => {
    const request = context.req.raw;
    const url = new URL(request.url);
    const isServiceRoute =
      url.pathname === "/systems/v1" || url.pathname.startsWith("/systems/v1/");
    if (!isServiceRoute) {
      if (request.method !== "GET") {
        return methodNotAllowed();
      }
      await next();
      return;
    }
    const authentication = await authenticateServiceRequest({
      request,
      secret: options.hmacSecret,
      nonceStore,
      now: now(),
    });
    if (!authentication.ok) {
      return authentication.reason === "replayed_nonce"
        ? jsonResponse(
            {
              error: {
                code: "replayed_nonce",
                message: "Service nonce was already used.",
              },
            },
            409,
          )
        : jsonResponse(
            {
              error: {
                code: "invalid_service_auth",
                message: "Service authentication failed.",
              },
            },
            401,
          );
    }
    if (request.method !== "GET") {
      return methodNotAllowed();
    }
    await next();
  });

  app.get("/api/v1/cases", (context) => {
    return publicCases(new URL(context.req.url));
  });
  app.get("/api/v1/cases/:caseId", (context) => {
    const caseId = context.req.param("caseId");
    if (!new RegExp(`^${UUID_PATTERN}$`).test(caseId)) {
      notFound();
    }
    return publicCase(caseId, new URL(context.req.url));
  });
  app.get("/systems/v1/crm/accounts/:accountId", (context) => {
    const accountId = context.req.param("accountId");
    if (!new RegExp(`^${UUID_PATTERN}$`).test(accountId)) {
      notFound();
    }
    return crmAccount(accountId, new URL(context.req.url), context.req.raw);
  });
  app.get("/systems/v1/billing/accounts/:accountId/subscription", (context) => {
    const accountId = context.req.param("accountId");
    if (!new RegExp(`^${UUID_PATTERN}$`).test(accountId)) {
      notFound();
    }
    return subscription(accountId, new URL(context.req.url), context.req.raw);
  });
  app.get("/systems/v1/billing/accounts/:accountId/invoices", (context) => {
    const accountId = context.req.param("accountId");
    if (!new RegExp(`^${UUID_PATTERN}$`).test(accountId)) {
      notFound();
    }
    return invoices(accountId, new URL(context.req.url), context.req.raw);
  });
  app.get(
    "/systems/v1/billing/invoices/:invoiceId/payment-attempts",
    (context) => {
      const invoiceId = context.req.param("invoiceId");
      if (!new RegExp(`^${UUID_PATTERN}$`).test(invoiceId)) {
        notFound();
      }
      return paymentAttempts(
        invoiceId,
        new URL(context.req.url),
        context.req.raw,
      );
    },
  );
  app.get("/systems/v1/policies/:policyKey", (context) => {
    const policyKey = context.req.param("policyKey");
    if (!/^[a-z0-9_]{1,80}$/.test(policyKey)) {
      notFound();
    }
    return policy(policyKey, new URL(context.req.url));
  });
  app.get("/systems/v1/status", (context) => {
    return status(new URL(context.req.url));
  });

  app.all("*", (context) => {
    if (context.req.method !== "GET") {
      return methodNotAllowed();
    }
    notFound();
  });

  app.onError((error) => {
    if (error instanceof HttpError) {
      return jsonResponse(
        { error: { code: error.code, message: error.message } },
        error.status,
      );
    }
    return jsonResponse(
      {
        error: {
          code: "fixture_unavailable",
          message: "Synthetic data is unavailable.",
        },
      },
      500,
    );
  });

  return {
    fetch(request: Request): Promise<Response> {
      return Promise.resolve(app.fetch(request));
    },
  };
}
