import assert from "node:assert/strict";
import test from "node:test";
import {
  createConfirmation,
  registerConfirmationBeforeApiCall,
  verifyConfirmation,
} from "./confirmation";
import { SafetyConfig } from "./types";

const config: SafetyConfig = {
  mutationsEnabled: true,
  allowUnsafeLegacyMutations: false,
  allowCreate: true,
  allowUpdate: true,
  allowDelete: false,
  allowRevert: false,
  allowCreateVersion: false,
  allowSetLatest: false,
  allowPublish: false,
  allowPublishNonLatest: false,
  allowUndelete: false,
  allowedAccountIds: new Set(["1"]),
  allowedContainerIds: new Set(["2"]),
  allowedWorkspaceIds: new Set(["3"]),
  maxOperationsPerRequest: 10,
  confirmationTtlSeconds: 900,
  confirmationSecret: "test-secret-that-is-long-enough",
};

test("confirmation is bound to hash, verb, stage and replay state", async () => {
  const created = await createConfirmation(
    config,
    "EXECUTE",
    "abc123",
    "TEST",
    {
      stage: "TEST",
      accountIds: ["1"],
      containerIds: ["2"],
      workspaceIds: ["3"],
      operationCount: 1,
    },
  );

  const verified = await verifyConfirmation(
    config,
    created.token,
    "EXECUTE",
    "abc123",
    "TEST",
  );
  assert.equal(verified.claims.hash, "abc123");

  registerConfirmationBeforeApiCall(verified.fingerprint);
  await assert.rejects(
    () =>
      verifyConfirmation(config, created.token, "EXECUTE", "abc123", "TEST"),
    /CONFIRMATION_REPLAYED/,
  );
});
