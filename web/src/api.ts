// The only way the UI talks to the server: same-origin JSON with the CSRF header on writes, plus the upload POST
// (to this server on a laptop, straight to the state bucket in the cloud: aws-deploy ADR-10).
import type { AuthMe, Session, UploadTicket } from "./types";

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

// --- sign-in (deployments with auth on, aws-deploy ADR-9; a laptop never answers 401) ----------------------------

const signedOut = new Set<() => void>();

/** Called whenever the server answers 401: the session ended, expired or its token was revoked. */
export function onSignedOut(fn: () => void): () => void {
  signedOut.add(fn);
  return () => signedOut.delete(fn);
}

function endSession() {
  session = null;
  signedOut.forEach((fn) => fn());
}

/** Sign in with an access token (no CSRF token yet: the server exempts this one write). */
export async function login(token: string): Promise<AuthMe> {
  const r = await fetch("/api/auth/login", {
    method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }),
  });
  const body = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok) throw new ApiError(r.status, body.error ?? `HTTP ${r.status}`);
  session = null;
  return body as AuthMe;
}

export async function logout(): Promise<void> {
  try {
    await post("/api/auth/logout");
  } finally {
    endSession();
  }
}

/** A 409 from an edit made on a page another user's edit has since changed (aws-deploy spec S2). */
export const isStale = (e: unknown) => e instanceof ApiError && e.status === 409 && e.message.startsWith("Changed by someone else");

async function parse<T>(r: Response): Promise<T> {
  const body = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (r.status === 401) endSession();
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

/**
 * Send a file in two steps (aws-deploy I6): ask for an upload ticket, then POST the file where it says, with
 * progress. Returns the upload id that `/api/import/by-upload` and `/api/rubric/drafts/by-upload` take. On a laptop
 * the ticket points at this server (CSRF header needed); in the cloud at a presigned S3 POST, which must get no
 * custom header (one would need a CORS preflight) and wants the file as the last field.
 */
export async function uploadFile(kind: "trace" | "rubric_source", file: File, onProgress?: (pct: number) => void): Promise<string> {
  const ticket = await post<UploadTicket>("/api/uploads", { kind, filename: file.name, size: file.size });
  const local = ticket.url.startsWith("/");
  const form = new FormData();
  for (const [k, v] of Object.entries(ticket.fields)) form.append(k, v);
  form.append("file", file);
  const csrf = local ? (await getSession()).csrf : null;
  await new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", ticket.url);
    if (csrf) xhr.setRequestHeader("X-CSRF-Token", csrf);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) return resolve();
      if (xhr.status === 401) endSession();
      let message = `Upload failed (HTTP ${xhr.status})`;
      if (local) {
        try { message = JSON.parse(xhr.responseText).error ?? message; } catch { /* not JSON */ }
      } else if (xhr.responseText.includes("EntityTooLarge")) message = "The file is over the limit.";
      else if (xhr.responseText.includes("AccessDenied")) message = "The upload link expired; try again.";
      reject(new ApiError(xhr.status, message));
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error"));
    xhr.send(form);
  });
  return ticket.upload_id;
}
