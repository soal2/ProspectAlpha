export class ApiError extends Error {
  readonly status: number;
  readonly error_code: string | null;

  constructor(status: number, message: string, error_code: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.error_code = error_code;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return (
    error instanceof ApiError ||
    (error instanceof Error &&
      error.name === "ApiError" &&
      "status" in error &&
      typeof (error as ApiError).status === "number")
  );
}

export function isNotFound(error: unknown): boolean {
  return isApiError(error) && error.status === 404;
}

export function isRetryableApiError(error: unknown): boolean {
  return isApiError(error) && error.status === 503;
}

export async function apiErrorFromResponse(response: Response): Promise<ApiError> {
  const text = await response.text().catch(() => "");
  let detail = "";
  let errorCode: string | null = null;
  if (text) {
    try {
      const parsed = JSON.parse(text) as { detail?: unknown; error_code?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
      else if (parsed.detail != null) detail = JSON.stringify(parsed.detail);
      if (typeof parsed.error_code === "string" && parsed.error_code) {
        errorCode = parsed.error_code;
      }
    } catch {
      detail = text;
    }
  }
  if (!detail) detail = response.statusText || "请求失败";
  const message =
    response.status === 503
      ? `${detail}（503，可重试）`
      : `Request failed (${response.status}): ${detail}`;
  return new ApiError(response.status, message, errorCode);
}
