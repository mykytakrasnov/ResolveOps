const encoder = new TextEncoder();
const MAX_CLOCK_SKEW_SECONDS = 300;
const NONCE_PATTERN = /^[A-Za-z0-9._~-]{8,128}$/;
const SIGNATURE_PATTERN = /^[0-9a-f]{64}$/;

export interface NonceStore {
  claim(
    nonce: string,
    expiresAtEpochSeconds: number,
    nowEpochSeconds: number,
  ): Promise<boolean>;
}

/** Replaceable single-isolate implementation; production can supply a durable store. */
export class InMemoryNonceStore implements NonceStore {
  readonly #expiresByNonce = new Map<string, number>();

  async claim(
    nonce: string,
    expiresAtEpochSeconds: number,
    nowEpochSeconds: number,
  ): Promise<boolean> {
    for (const [storedNonce, expiresAt] of this.#expiresByNonce) {
      if (expiresAt < nowEpochSeconds) {
        this.#expiresByNonce.delete(storedNonce);
      }
    }
    if (this.#expiresByNonce.has(nonce)) {
      return false;
    }
    this.#expiresByNonce.set(nonce, expiresAtEpochSeconds);
    return true;
  }
}

export interface ServiceSignatureInput {
  secret: string;
  method: string;
  pathAndQuery: string;
  timestamp: string;
  nonce: string;
  accountId?: string | undefined;
}

function canonicalRequest(
  input: Omit<ServiceSignatureInput, "secret">,
): string {
  return [
    input.method.toUpperCase(),
    input.pathAndQuery,
    input.timestamp,
    input.nonce,
    input.accountId ?? "",
  ].join("\n");
}

function bytesToHex(bytes: ArrayBuffer): string {
  return [...new Uint8Array(bytes)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export async function signServiceRequest(
  input: ServiceSignatureInput,
): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(input.secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    encoder.encode(canonicalRequest(input)),
  );
  return bytesToHex(signature);
}

function constantTimeEqual(left: string, right: string): boolean {
  let difference = left.length ^ right.length;
  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    difference |=
      (left.charCodeAt(index) || 0) ^ (right.charCodeAt(index) || 0);
  }
  return difference === 0;
}

export type AuthenticationResult =
  | { ok: true }
  | { ok: false; reason: "invalid_auth" | "replayed_nonce" };

export async function authenticateServiceRequest(options: {
  request: Request;
  secret: string;
  nonceStore: NonceStore;
  now: Date;
}): Promise<AuthenticationResult> {
  const timestamp = options.request.headers.get("X-Service-Timestamp") ?? "";
  const nonce = options.request.headers.get("X-Service-Nonce") ?? "";
  const signature = options.request.headers.get("X-Service-Signature") ?? "";
  const accountId =
    options.request.headers.get("X-Service-Account-ID") ?? undefined;
  if (!/^\d{1,12}$/.test(timestamp) || !NONCE_PATTERN.test(nonce)) {
    return { ok: false, reason: "invalid_auth" };
  }
  const requestEpochSeconds = Number(timestamp);
  const nowEpochSeconds = Math.floor(options.now.getTime() / 1_000);
  if (
    !Number.isSafeInteger(requestEpochSeconds) ||
    Math.abs(nowEpochSeconds - requestEpochSeconds) > MAX_CLOCK_SKEW_SECONDS
  ) {
    return { ok: false, reason: "invalid_auth" };
  }
  if (!SIGNATURE_PATTERN.test(signature)) {
    return { ok: false, reason: "invalid_auth" };
  }

  const url = new URL(options.request.url);
  const expected = await signServiceRequest({
    secret: options.secret,
    method: options.request.method,
    pathAndQuery: `${url.pathname}${url.search}`,
    timestamp,
    nonce,
    accountId,
  });
  if (!constantTimeEqual(signature, expected)) {
    return { ok: false, reason: "invalid_auth" };
  }

  const claimed = await options.nonceStore.claim(
    nonce,
    requestEpochSeconds + MAX_CLOCK_SKEW_SECONDS,
    nowEpochSeconds,
  );
  return claimed ? { ok: true } : { ok: false, reason: "replayed_nonce" };
}
