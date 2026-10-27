// Minimal API client. The token comes from the URL fragment printed by `lucid serve`
// (#token=...), is kept in sessionStorage for this tab only, and is sent as a header.

const KEY = "lucid.token";

export function initToken(): string | null {
  const m = window.location.hash.match(/token=([^&]+)/);
  if (m) {
    try { sessionStorage.setItem(KEY, decodeURIComponent(m[1])); } catch { /* storage unavailable */ }
    history.replaceState(null, "", window.location.pathname + window.location.search);
    return decodeURIComponent(m[1]);
  }
  try { return sessionStorage.getItem(KEY); } catch { return null; }
}

export function setToken(t: string) {
  try { sessionStorage.setItem(KEY, t); } catch { /* ignore */ }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

let token: string | null = null;
export function useTokenValue(t: string | null) { token = t; }

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", "X-Lucid-Token": token ?? "", ...(init.headers || {}) },
    });
  } catch {
    throw new ApiError(0, "disconnected: the Lucid service is not reachable");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = j.detail || j.error || detail; } catch { /* text */ }
    throw new ApiError(res.status, String(detail));
  }
  return res.json() as Promise<T>;
}

export const post = <T,>(path: string, body: unknown) => api<T>(path, { method: "POST", body: JSON.stringify(body) });
export const del = <T,>(path: string) => api<T>(path, { method: "DELETE" });
