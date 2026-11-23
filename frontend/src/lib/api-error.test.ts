import { describe, expect, test } from "bun:test";

import {
  ApiError,
  apiErrorFromResponse,
  isRetryableApiError,
  withResourceFallback,
} from "./api-error";

describe("api errors", () => {
  test("404 trace or backtest resolves to the empty state", async () => {
    const result = await withResourceFallback(
      Promise.reject(new ApiError(404, "missing", "not_found")),
      null,
    );
    expect(result).toBeNull();
  });

  test("503 stays a visible retryable error instead of being swallowed", async () => {
    const response = new Response(
      JSON.stringify({ detail: "数据库暂不可用", error_code: "database_unavailable" }),
      { status: 503, headers: { "content-type": "application/json" } },
    );
    const error = await apiErrorFromResponse(response);
    expect(error.status).toBe(503);
    expect(error.error_code).toBe("database_unavailable");
    expect(error.message).toContain("可重试");
    expect(isRetryableApiError(error)).toBe(true);
    await expect(withResourceFallback(Promise.reject(error), null)).rejects.toMatchObject({
      status: 503,
      error_code: "database_unavailable",
    });
  });

  test("a returned promise is awaited, so the 404 handler actually runs", async () => {
    let settled = false;
    const request = Promise.reject(new ApiError(404, "missing", "not_found")).finally(() => {
      settled = true;
    });
    const result = await withResourceFallback(request, null);
    expect(result).toBeNull();
    expect(settled).toBe(true);
  });
});
