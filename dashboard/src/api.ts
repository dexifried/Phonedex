import type { Session } from "./types";
let csrf = "";
export class ApiError extends Error {
  constructor(message: string, public readonly status: number) { super(message); }
}
export function setSession(s: Session) {
  csrf = s.csrf || "";
}
export async function api<T = any>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const r = await fetch(`/api/${path}`, {
    method,
    credentials: "same-origin",
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(method !== "GET" ? { "X-CSRF-Token": csrf } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    let message = `Request failed (${r.status})`;
    try {
      const d = await r.json();
      message =
        typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail || d);
    } catch {}
    throw new ApiError(message, r.status);
  }
  return r.status === 204 ? (undefined as T) : await r.json();
}
export async function authenticate(path: string, password: string, extra?:{pairing_token:string}) {
  await api(path, "POST", { password,...extra });
  const s = await api<Session>("auth/session");
  setSession(s);
  return s;
}
export async function download(path: string, filename: string) {
  const r = await fetch(`/api/${path}`, { credentials: "same-origin" });
  if (!r.ok) {
    const d = await r.json().catch(() => ({ detail: "Export failed" }));
    throw new Error(d.detail);
  }
  const u = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = u;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(u);
}
export function saveText(text: string, name: string, type = "text/plain") {
  const u = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = u;
  a.download = name;
  a.click();
  URL.revokeObjectURL(u);
}
