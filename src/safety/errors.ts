export function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  if (typeof error === "string") {
    return error;
  }
  try {
    return JSON.stringify(error);
  } catch {
    return "Unknown error";
  }
}

export function structuredError(
  errorType: string,
  message: string,
  details: Record<string, unknown> = {},
): {
  isError: true;
  content: Array<{ type: "text"; text: string }>;
} {
  return {
    isError: true,
    content: [
      {
        type: "text",
        text: JSON.stringify(
          {
            error_type: errorType,
            message,
            ...details,
          },
          null,
          2,
        ),
      },
    ],
  };
}
