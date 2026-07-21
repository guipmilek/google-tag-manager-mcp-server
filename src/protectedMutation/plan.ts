import {
  ConfirmationScope,
  NormalizedMutationOperation,
  sha256Hex,
} from "../safety";

export function confirmationScope(
  stage: string,
  operations: NormalizedMutationOperation[],
): ConfirmationScope {
  return {
    stage,
    accountIds: [...new Set(operations.map((item) => item.accountId))].sort(),
    containerIds: [...new Set(operations.map((item) => item.containerId))].sort(),
    workspaceIds: [...new Set(operations.map((item) => item.workspaceId))].sort(),
    operationCount: operations.length,
  };
}

export async function mutationHash(
  stage: string,
  operations: NormalizedMutationOperation[],
): Promise<string> {
  return sha256Hex({
    operation_hash_version: 1,
    stage,
    atomic: false,
    execution_strategy: "SEQUENTIAL_STOP_ON_FIRST_ERROR",
    operations,
  });
}
