import { API_BASE_URL } from "./env";

export interface AuthSession {
  authenticated: boolean;
  auth_disabled: boolean;
  csrf_token?: string | null;
  expires_at?: number;
}

let csrfTokenValue = "";

export function csrfToken(): string {
  return csrfTokenValue;
}

export function rememberCsrf(token: string | null | undefined) {
  csrfTokenValue = token ?? "";
}

export function apiRequestHeaders(base?: HeadersInit, method = "GET"): Headers {
  const headers = new Headers(base);
  const verb = method.toUpperCase();
  if (verb !== "GET" && verb !== "HEAD" && verb !== "OPTIONS" && csrfTokenValue) {
    headers.set("X-CSRF-Token", csrfTokenValue);
  }
  return headers;
}

export async function refreshSession(): Promise<AuthSession> {
  const response = await fetch(`${API_BASE_URL}/auth/session`, {
    method: "GET",
    credentials: "include",
    headers: { accept: "application/json" },
  });
  if (!response.ok) {
    rememberCsrf(null);
    return { authenticated: false, auth_disabled: false };
  }
  const session = (await response.json()) as AuthSession;
  rememberCsrf(session.csrf_token);
  return session;
}

export async function login(username: string, password: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: apiRequestHeaders({ "content-type": "application/json", accept: "application/json" }, "POST"),
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(payload.detail || "登录失败");
  }
  const session = (await response.json()) as AuthSession;
  rememberCsrf(session.csrf_token);
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE_URL}/auth/logout`, {
    method: "POST",
    credentials: "include",
    headers: apiRequestHeaders({ accept: "application/json" }, "POST"),
  });
  rememberCsrf(null);
}

export function redirectToLogin() {
  if (typeof window === "undefined" || window.location.pathname.endsWith("/login")) return;
  const base = import.meta.env.BASE_URL ?? "/";
  window.location.assign(`${base}login`);
}
