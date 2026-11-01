// aws-deploy slice 7: sign-in, 401s, the two-step upload, edit conflicts, interrupted runs, the receiver flag.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const SESSION = { csrf: "c", configs: ["offline"], prompts: [], models: [], tasks: ["adb"], rubric: null, aws_profile: null, receiver: true };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

beforeEach(() => {
  vi.resetModules();  // api.ts caches the session per module
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("sign-in", () => {
  it("shows only the sign-in page until a token works, with the approved messages", async () => {
    const { AuthGate } = await import("./components/SignIn");
    let signedIn = false;
    const replies: Record<string, Response> = {};
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/auth/login") {
        const { token } = JSON.parse(String(init?.body));
        if (token === "good") { signedIn = true; return json({ name: "ana" }); }
        return replies[token] ?? json({ error: "That token isn't valid." }, 401);
      }
      return signedIn ? json({ ...SESSION, user: "ana" }) : json({ error: "Sign in required" }, 401);
    }));
    replies.busy = json({ error: "Too many requests" }, 429);
    replies.down = json({ error: "Internal Server Error" }, 500);
    render(<AuthGate><p>the app</p></AuthGate>);
    const field = await screen.findByLabelText("Access token");
    expect(screen.queryByText("the app")).toBeNull();
    for (const [token, message] of [["bad", "That token isn't valid."], ["busy", "Too many attempts; wait a minute."],
      ["down", "Sign-in is unavailable; try again shortly."]]) {
      fireEvent.change(field, { target: { value: token } });
      fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
      expect((await screen.findByRole("alert")).textContent).toContain(message);
    }
    fireEvent.change(field, { target: { value: "good" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("the app")).toBeTruthy();
  });

  it("goes back to sign-in when any later request answers 401", async () => {
    const { AuthGate } = await import("./components/SignIn");
    const { get } = await import("./api");
    let valid = true;
    vi.stubGlobal("fetch", vi.fn(async () => (valid ? json(SESSION) : json({ error: "Sign in required" }, 401))));
    render(<AuthGate><p>the app</p></AuthGate>);
    expect(await screen.findByText("the app")).toBeTruthy();
    valid = false;  // the token was revoked
    await act(async () => { await get("/api/runs").catch(() => {}); });
    expect(await screen.findByLabelText("Access token")).toBeTruthy();
    expect(screen.queryByText("the app")).toBeNull();
  });

  it("names the signed-in user in the header, and signs out", async () => {
    const { Shell } = await import("./components/Shell");
    const { AuthGate } = await import("./components/SignIn");
    const { ToastProvider } = await import("./components/toasts");
    let signedIn = true;
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
      if (url === "/api/auth/logout") { signedIn = false; return json({ ok: true }); }
      return signedIn ? json({ ...SESSION, user: "ana" }) : json({ error: "Sign in required" }, 401);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MemoryRouter><ToastProvider><AuthGate><Shell><p>page</p></Shell></AuthGate></ToastProvider></MemoryRouter>);
    expect(await screen.findAllByText("ana")).toHaveLength(2);  // the desktop header and the phone nav row (CSS shows one)
    expect(screen.getAllByText(/Signed in as/)).toHaveLength(2);
    fireEvent.click(screen.getAllByRole("button", { name: "Sign out" })[0]);
    expect(await screen.findByLabelText("Access token")).toBeTruthy();
    const logout = fetchMock.mock.calls.find(([u]) => u === "/api/auth/logout")!;
    expect((logout[1] as RequestInit).headers).toMatchObject({ "X-CSRF-Token": "c" });
  });
});

/** A stand-in XMLHttpRequest that records what was sent and answers `status`. */
function fakeXhr(status: number, responseText = "") {
  const sent: { url: string; headers: Record<string, string>; form: FormData | null }[] = [];
  class Xhr {
    status = 0; responseText = ""; url = ""; headers: Record<string, string> = {};
    upload = { onprogress: null as ((e: ProgressEvent) => void) | null };
    onload: (() => void) | null = null; onerror: (() => void) | null = null;
    open(_m: string, url: string) { this.url = url; }
    setRequestHeader(k: string, v: string) { this.headers[k] = v; }
    send(form: FormData) {
      sent.push({ url: this.url, headers: this.headers, form });
      this.status = status; this.responseText = responseText;
      queueMicrotask(() => this.onload?.());
    }
  }
  vi.stubGlobal("XMLHttpRequest", Xhr);
  return sent;
}

describe("two-step uploads", () => {
  it("posts a cloud upload to the bucket with its fields first and no custom header", async () => {
    const { uploadFile } = await import("./api");
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => url === "/api/uploads"
      ? json({ upload_id: "abc", url: "https://b.s3.us-west-2.amazonaws.com/", fields: { key: "uploads/abc", policy: "p" } }, 201)
      : json(SESSION));
    vi.stubGlobal("fetch", fetchMock);
    const sent = fakeXhr(204);
    const file = new File(["{}"], "trace.json");
    expect(await uploadFile("trace", file)).toBe("abc");
    expect(JSON.parse(String((fetchMock.mock.calls.find(([u]) => u === "/api/uploads")![1] as RequestInit).body)))
      .toEqual({ kind: "trace", filename: "trace.json", size: 2 });
    expect(sent[0].url).toBe("https://b.s3.us-west-2.amazonaws.com/");
    expect(sent[0].headers).toEqual({});
    expect([...sent[0].form!.keys()]).toEqual(["key", "policy", "file"]);
  });

  it("sends a laptop upload to this server with the CSRF header, and explains S3's refusals", async () => {
    const { uploadFile } = await import("./api");
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url === "/api/uploads"
      ? json({ upload_id: "abc", url: "/api/uploads/abc", fields: {} }, 201) : json(SESSION)));
    const sent = fakeXhr(201, "{}");
    await uploadFile("rubric_source", new File(["x"], "r.md"));
    expect(sent[0].headers).toEqual({ "X-CSRF-Token": "c" });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url === "/api/uploads"
      ? json({ upload_id: "abc", url: "https://b.s3.us-west-2.amazonaws.com/", fields: {} }, 201) : json(SESSION)));
    fakeXhr(400, "<Error><Code>EntityTooLarge</Code></Error>");
    await expect(uploadFile("trace", new File(["x"], "t.json"))).rejects.toThrow("The file is over the limit.");
  });
});

describe("cloud differences on pages", () => {
  it("hides the live receiver when the deployment has none", async () => {
    const { default: Import } = await import("./pages/Import");
    const fetchMock = vi.fn(async (_url: string) => json({ ...SESSION, receiver: false }));
    vi.stubGlobal("fetch", fetchMock);
    render(<MemoryRouter><Import /></MemoryRouter>);
    expect(await screen.findByText("Upload a trace")).toBeTruthy();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByText("Live receiver")).toBeNull();
    expect(fetchMock.mock.calls.some(([u]) => String(u).startsWith("/api/receiver"))).toBe(false);
  });

  it("shows an interrupted run as interrupted, with a note and a way to score it", async () => {
    const { Board } = await import("./components/Board");
    const { RunDrawer } = await import("./pages/Runs");
    const onEvaluate = vi.fn();
    render(<MemoryRouter><Board runs={[{ run_id: "r1", prompt_id: "adb-001", model: "m", status: "interrupted", headline: "interrupted",
      column: "attention", unreadable: false, kpi: { spent: 0, overall: null } }]} onEvaluate={onEvaluate} /></MemoryRouter>);
    expect(screen.getByText("interrupted")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Score it/ }));
    expect(onEvaluate).toHaveBeenCalledWith("r1");
    cleanup();
    const detail = {
      run: { run_id: "r1", prompt_id: "adb-001", prompt: "Map cropland", model: { id: "m" }, status: "interrupted" },
      scores: null, report_html: null, rubric_info: null, evaluating: false, interrupted: false,
      m: { live: false, status: "interrupted", elapsed: "15:00", start_ms: null, gauges: [], cost: 0.01, tokens_out: "5",
        tools_ok: 2, tools_err: 0, servers: [], rows: [], working: null, axis: [], feed: [], seen: 0 },
    };
    vi.stubGlobal("fetch", vi.fn(async () => json(detail)));
    render(<MemoryRouter><RunDrawer id="r1" onEvaluate={onEvaluate} /></MemoryRouter>);
    expect(await screen.findByText(/This run was interrupted/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Score this run" })).toBeTruthy();
  });

  it("sends the loaded project version with an edit and offers a reload on a conflict", async () => {
    const { default: PromptEdit } = await import("./pages/PromptEdit");
    const { ToastProvider } = await import("./components/toasts");
    const { Route, Routes } = await import("react-router-dom");
    const detail = { prompt: { prompt_id: "adb-009", prompt: "Old.", archived: false, stages: [] }, locked: false,
      runs_total: 0, runs: [], mean: null, next_version_id: null, base_version: 7 };
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "PUT") return json({ error: "Changed by someone else; reload" }, 409);
      if (url.startsWith("/api/prompts/tools")) return json({ tools: [], listed_at: null, refreshing: false, errors: [] });
      if (url.startsWith("/api/prompts/")) return json(detail);
      return json(SESSION);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MemoryRouter initialEntries={["/prompts/adb-009"]}><ToastProvider><Routes>
      <Route path="/prompts/:id" element={<PromptEdit />} /></Routes></ToastProvider></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText("Prompt text"), { target: { value: "New." } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Someone else changed this");
    const put = fetchMock.mock.calls.find(([, i]) => (i as RequestInit | undefined)?.method === "PUT")!;
    expect(JSON.parse(String((put[1] as RequestInit).body))).toMatchObject({ prompt: "New.", base_version: 7 });
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    await waitFor(() => expect((screen.getByLabelText("Prompt text") as HTMLTextAreaElement).value).toBe("Old."));
  });
});

describe("the app before sign-in", () => {
  it("asks only who is signed in: nothing polls the API until there is a session", async () => {
    const { App } = await import("./App");
    const fetchMock = vi.fn(async (_url: string) => json({ error: "Sign in required" }, 401));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    expect(await screen.findByLabelText("Access token")).toBeTruthy();
    await new Promise((r) => setTimeout(r, 50));
    expect(fetchMock.mock.calls.map(([u]) => u)).toEqual(["/api/session"]);
  });
});

describe("a run that has just been started", () => {
  it("says it is starting while its job is live, and not found otherwise", async () => {
    const { RunDrawer } = await import("./pages/Runs");
    const notFound = () => json({ error: "Run not found" }, 404);
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url === "/api/jobs"
      ? json({ jobs: [{ id: "j", kind: "run", run_id: "r9", label: "", alive: true, exit_code: null, failed: false, finished_at: null, message: null }] })
      : notFound()));
    render(<MemoryRouter><RunDrawer id="r9" onEvaluate={() => {}} /></MemoryRouter>);
    expect(await screen.findByText(/Starting the run/)).toBeTruthy();
    expect(screen.queryByText("✗ Run not found")).toBeNull();
    cleanup();
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url === "/api/jobs" ? json({ jobs: [] }) : notFound()));
    render(<MemoryRouter><RunDrawer id="nope" onEvaluate={() => {}} /></MemoryRouter>);
    expect(await screen.findByText(/Run not found/)).toBeTruthy();
    expect(screen.queryByText(/Starting the run/)).toBeNull();
  });
});

describe("tabs inside a form", () => {
  it("switch the tab without submitting the form (the Rubric page's source tabs)", async () => {
    const { Tabs } = await import("./components/ui");
    const submit = vi.fn((e: Event) => e.preventDefault());
    const onChange = vi.fn();
    render(<form onSubmit={(e) => submit(e.nativeEvent)}><Tabs value="file" onChange={onChange}
      tabs={[{ id: "file", label: "File" }, { id: "text", label: "Text" }]} /></form>);
    fireEvent.click(screen.getByRole("tab", { name: "Text" }));
    expect(onChange).toHaveBeenCalledWith("text");
    expect(submit).not.toHaveBeenCalled();
  });
});
