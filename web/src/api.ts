// The only way the UI talks to the server: same-origin JSON with the CSRF header on writes.
import type { Session } from "./types";

export class ApiError extends Error {
  status: number;
  field: string | null;
  constructor(status: number, message: string, field: string | null = null) {
    super(message);
    this.status = status;
    this.field = field;
  }
}

let session: Promise<Session> | null = null;
const listeners = new Set<(s: Session) => void>();

/** The session (configs, prompts, ...); `force` refetches it and tells every subscriber (increment 13). */
export function getSession(force = false): Promise<Session> {
  if (!session || force) {
    session = get<Session>("/api/session");
    if (force) session.then((s) => listeners.forEach((fn) => fn(s))).catch(() => {});
  }
  return session;
}

export function onSessionChange(fn: (s: Session) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

async function parse<T>(r: Response): Promise<T> {
  const body = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok) throw new ApiError(r.status, body.error ?? `HTTP ${r.status}`, body.field ?? null);
  return body as T;
}

export async function get<T>(url: string): Promise<T> {
  return parse<T>(await fetch(url, { credentials: "same-origin", cache: "no-store" }));
}

async function write<T>(method: string, url: string, body?: unknown, retried = false): Promise<T> {
  const { csrf } = await getSession();
  const r = await fetch(url, {
    method,
    credentials: "same-origin",
    headers: { "X-CSRF-Token": csrf, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (r.status === 403 && !retried) {  // the server restarted and rotated its token
    await getSession(true);
    return write<T>(method, url, body, true);
  }
  return parse<T>(r);
}

export const post = <T>(url: string, body?: unknown) => write<T>("POST", url, body);
export const put = <T>(url: string, body?: unknown) => write<T>("PUT", url, body);
export const del = <T>(url: string) => write<T>("DELETE", url);

/** Multipart upload with progress (fetch cannot report upload progress, XHR can). */
export async function upload<T>(url: string, form: FormData, onProgress?: (pct: number) => void): Promise<T> {
  const { csrf } = await getSession();
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("X-CSRF-Token", csrf);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => {
      let body: { error?: string; field?: string } = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = { error: `HTTP ${xhr.status}` };
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as T);
      else reject(new ApiError(xhr.status, body.error ?? `HTTP ${xhr.status}`, body.field ?? null));
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error"));
    xhr.send(form);
  });
}
