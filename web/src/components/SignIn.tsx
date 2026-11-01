// aws-deploy ADR-9: a deployment with auth on shows only this page until the browser holds a session. On a laptop the
// server never answers 401, so the gate passes straight through.
import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { ApiError, getSession, login, onSignedOut } from "../api";
import { PageHeader, Panel, Skeleton } from "./ui";

const MESSAGES: Record<number, string> = {
  401: "That token isn't valid.",
  429: "Too many attempts; wait a minute.",
};

export function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(token.trim());
      onSignedIn();
    } catch (err) {
      setError(MESSAGES[err instanceof ApiError ? err.status : 0] ?? "Sign-in is unavailable; try again shortly.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="mx-auto max-w-md space-y-6 px-4 py-16">
      <div className="flex items-center gap-2 font-semibold">
        <img src="/assets/brand/logo-light-v1.png" width={32} height={32} alt="" className="dark:hidden" />
        <img src="/assets/brand/logo-dark-v1.png" width={32} height={32} alt="" className="hidden dark:block" />
        <span>Foundry<span className="font-normal text-slate-400"> / eval</span></span>
      </div>
      <PageHeader title="Sign in" caption="Use the access token the operator gave you." />
      <Panel className="p-5">
        <form onSubmit={submit} className="space-y-4">
          <label className="field">Access token
            <input className="input font-mono" type="password" autoComplete="current-password" autoFocus value={token}
              aria-invalid={error ? true : undefined} aria-describedby={error ? "signin-error" : undefined}
              onChange={(e) => setToken(e.target.value)} />
          </label>
          {error && <p id="signin-error" role="alert" className="text-sm text-rose-600">✗ {error}</p>}
          <button className="btn-primary" disabled={busy || !token.trim()}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
      </Panel>
    </main>
  );
}

/** Renders the app once the session loads, the sign-in page on a 401 (now or after any later request). */
export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"checking" | "in" | "out">("checking");
  useEffect(() => {
    const off = onSignedOut(() => setState("out"));
    getSession().then(() => setState("in"))
      .catch((e) => setState(e instanceof ApiError && e.status === 401 ? "out" : "in"));  // other errors: the pages show them
    return off;
  }, []);
  if (state === "checking") return <div className="p-8"><Skeleton className="h-64" /></div>;
  const signedIn = () => getSession(true).then(() => setState("in"),
    (e) => { if (!(e instanceof ApiError && e.status === 401)) setState("in"); });
  if (state === "out") return <SignIn onSignedIn={signedIn} />;
  return <>{children}</>;
}
