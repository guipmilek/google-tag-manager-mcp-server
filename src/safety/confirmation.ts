import { sha256TextHex } from "./canonicalJson";
import {
  ConfirmationClaims,
  ConfirmationScope,
  ConfirmationVerb,
  SafetyConfig,
} from "./types";

const usedTokenFingerprints = new Set<string>();

function encodeBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function decodeBase64Url(value: string): Uint8Array {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

async function importHmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

function randomNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return encodeBase64Url(bytes);
}

export async function createConfirmation(
  config: SafetyConfig,
  verb: ConfirmationVerb,
  operationHash: string,
  stage: string,
  scope: ConfirmationScope,
): Promise<{ token: string; claims: ConfirmationClaims; expiresAt: string }> {
  if (!config.confirmationSecret) {
    throw new Error("GTM_CONFIRMATION_SECRET is not configured");
  }

  const now = Math.floor(Date.now() / 1000);
  const claims: ConfirmationClaims = {
    v: 1,
    verb,
    hash: operationHash,
    stage,
    iat: now,
    exp: now + config.confirmationTtlSeconds,
    nonce: randomNonce(),
    scope,
  };

  const payload = encodeBase64Url(
    new TextEncoder().encode(JSON.stringify(claims)),
  );
  const key = await importHmacKey(config.confirmationSecret);
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(payload),
  );
  const token = `${verb} ${operationHash}.${payload}.${encodeBase64Url(
    new Uint8Array(signature),
  )}`;

  return {
    token,
    claims,
    expiresAt: new Date(claims.exp * 1000).toISOString(),
  };
}

export async function verifyConfirmation(
  config: SafetyConfig,
  token: string,
  expectedVerb: ConfirmationVerb,
  expectedHash: string,
  expectedStage: string,
): Promise<{
  claims: ConfirmationClaims;
  fingerprint: string;
}> {
  if (!config.confirmationSecret) {
    throw new Error("GTM_CONFIRMATION_SECRET is not configured");
  }

  const prefix = `${expectedVerb} ${expectedHash}.`;
  if (!token.startsWith(prefix)) {
    throw new Error("CONFIRMATION_MISMATCH");
  }

  const tokenBody = token.slice(`${expectedVerb} `.length);
  const parts = tokenBody.split(".");
  if (parts.length !== 3) {
    throw new Error("INVALID_CONFIRMATION");
  }

  const [hash, payload, signature] = parts;
  if (hash !== expectedHash) {
    throw new Error("CONFIRMATION_MISMATCH");
  }

  const key = await importHmacKey(config.confirmationSecret);
  const valid = await crypto.subtle.verify(
    "HMAC",
    key,
    decodeBase64Url(signature),
    new TextEncoder().encode(payload),
  );
  if (!valid) {
    throw new Error("INVALID_CONFIRMATION");
  }

  const claims = JSON.parse(
    new TextDecoder().decode(decodeBase64Url(payload)),
  ) as ConfirmationClaims;

  if (
    claims.v !== 1 ||
    claims.verb !== expectedVerb ||
    claims.hash !== expectedHash ||
    claims.stage !== expectedStage
  ) {
    throw new Error("CONFIRMATION_MISMATCH");
  }

  const now = Math.floor(Date.now() / 1000);
  if (claims.exp <= now) {
    throw new Error("CONFIRMATION_EXPIRED");
  }

  const fingerprint = (await sha256TextHex(token)).slice(0, 16);
  if (usedTokenFingerprints.has(fingerprint)) {
    throw new Error("CONFIRMATION_REPLAYED");
  }

  return { claims, fingerprint };
}

export function registerConfirmationBeforeApiCall(fingerprint: string): void {
  usedTokenFingerprints.add(fingerprint);
}

export function replayProtectionDescription(): string {
  return "BEST_EFFORT_PROCESS_LOCAL";
}
