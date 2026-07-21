import {
  JsonObject,
  NormalizedMutationOperation,
  errorMessage,
} from "../safety";
import { getCollection, RESOURCE_METADATA } from "./model";

function resolveResponsePath(
  operation: NormalizedMutationOperation,
  responseData: JsonObject,
): string {
  if (typeof responseData.path === "string") {
    return responseData.path;
  }
  const idField = RESOURCE_METADATA[operation.resource].idField;
  const id = responseData[idField];
  if (typeof id === "string" && id) {
    return `${operation.parent}/${RESOURCE_METADATA[operation.resource].plural}/${id}`;
  }
  if (operation.path) {
    return operation.path;
  }
  return operation.parent;
}

export async function executeOne(
  client: any,
  operation: NormalizedMutationOperation,
): Promise<{ resourceName: string; response: JsonObject | null }> {
  const collection = getCollection(client, operation.resource);

  if (operation.action === "create") {
    const response = await collection.create({
      parent: operation.parent,
      requestBody: operation.data || {},
    });
    const data = (response.data || {}) as JsonObject;
    return { resourceName: resolveResponsePath(operation, data), response: data };
  }

  if (!operation.path) {
    throw new Error("resource path is unavailable");
  }

  if (operation.action === "update") {
    const response = await collection.update({
      path: operation.path,
      fingerprint: operation.resourceFingerprint || undefined,
      requestBody: operation.data || {},
    });
    const data = (response.data || {}) as JsonObject;
    return { resourceName: resolveResponsePath(operation, data), response: data };
  }

  if (operation.action === "remove") {
    await collection.delete({ path: operation.path });
    return { resourceName: operation.path, response: null };
  }

  const response = await collection.revert({
    path: operation.path,
    fingerprint: operation.resourceFingerprint || undefined,
  });
  const data = (response.data || {}) as JsonObject;
  const nested = data[operation.resource];
  const resourceData =
    nested && typeof nested === "object" ? (nested as JsonObject) : data;
  return {
    resourceName: resolveResponsePath(operation, resourceData),
    response: data,
  };
}

export async function verifyCompletedOperation(
  client: any,
  operation: NormalizedMutationOperation,
  resourceName: string,
): Promise<Record<string, unknown>> {
  const collection = getCollection(client, operation.resource);

  if (operation.action === "remove") {
    try {
      await collection.get({ path: resourceName });
      return {
        resource_name: resourceName,
        expected: "NOT_FOUND",
        observed: "STILL_READABLE",
        verified: false,
      };
    } catch (error) {
      const message = errorMessage(error);
      const notFound = /404|not found/i.test(message);
      return {
        resource_name: resourceName,
        expected: "NOT_FOUND",
        observed: notFound ? "NOT_FOUND" : "READ_FAILED",
        verified: notFound,
        warning: notFound ? null : message,
      };
    }
  }

  try {
    const response = await collection.get({ path: resourceName });
    return {
      resource_name: resourceName,
      expected: "READABLE",
      observed: "READABLE",
      verified: true,
      fingerprint:
        typeof response.data?.fingerprint === "string"
          ? response.data.fingerprint
          : null,
    };
  } catch (error) {
    return {
      resource_name: resourceName,
      expected: "READABLE",
      observed: "READ_FAILED",
      verified: false,
      warning: errorMessage(error),
    };
  }
}

export function classifyExecutionError(error: unknown): {
  errorType: string;
  executionMayHaveCompleted: boolean;
} {
  const candidate = error as {
    code?: number | string;
    response?: { status?: number };
  };
  const status = Number(candidate?.response?.status || candidate?.code || 0);
  if (status >= 400 && status < 500) {
    return {
      errorType: "GOOGLE_TAG_MANAGER_API_ERROR",
      executionMayHaveCompleted: false,
    };
  }
  return {
    errorType: "TRANSPORT_OR_CONNECTOR_ERROR",
    executionMayHaveCompleted: true,
  };
}
