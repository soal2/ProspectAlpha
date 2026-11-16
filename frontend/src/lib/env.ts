export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

/** Shared deployment token. Empty leaves requests unchanged for local development. */
export const API_AUTH_TOKEN = import.meta.env.VITE_API_AUTH_TOKEN ?? "";

export function apiAuthHeaders(base?: HeadersInit): Headers {
  const headers = new Headers(base);
  if (API_AUTH_TOKEN) {
    headers.set("Authorization", `Bearer ${API_AUTH_TOKEN}`);
  }
  return headers;
}
