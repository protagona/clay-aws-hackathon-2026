import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Board } from "./components/Board";
import { jobToasts } from "./components/toasts";
import { fieldOf, RubricForm } from "./pages/RubricDraft";
import type { Card, Job, Rubric } from "./types";
import { boardInterval, runInterval } from "./usePoll";

afterEach(cleanup);

const card = (run_id: string, column: Card["column"], extra: Partial<Card> = {}): Card =>
  ({ run_id, prompt_id: run_id, model: "us.moonshotai.kimi-k3", status: "succeeded", headline: "x", column, ...extra });

describe("Board", () => {  // T2: columns come from the API's `column`, never computed here
  it("puts each card in the column the API named", () => {
    render(<MemoryRouter><Board runs={[card("a", "running", { status: "running" }), card("b", "scored", { overall: 0.91 }),
      card("c", "attention", { headline: "not evaluated" })]} onEvaluate={() => {}} /></MemoryRouter>);
    expect(within(screen.getByRole("region", { name: "Running" })).getByText("a")).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "Scored" })).getByRole("img", { name: "overall .91" })).toBeTruthy();
    const attention = screen.getByRole("region", { name: "Needs attention" });
    expect(within(attention).getByText("c")).toBeTruthy();
    expect(within(attention).getByRole("button", { name: /Evaluate/ })).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "Evaluating" })).getByText("Nothing here")).toBeTruthy();
  });

  it("offers Evaluate only on cards that need it", () => {
    const onEvaluate = vi.fn();
    render(<MemoryRouter><Board runs={[card("c", "attention", { headline: "not evaluated" }), card("u", "attention", { unreadable: true })]} onEvaluate={onEvaluate} /></MemoryRouter>);
    const buttons = screen.getAllByRole("button", { name: /Evaluate/ });
    expect(buttons).toHaveLength(1);
    buttons[0].click();
    expect(onEvaluate).toHaveBeenCalledWith("c");
  });
});

describe("polling cadence", () => {  // design: board 2 s live / 5 s idle; run 1 s live, then stop
  it("follows the design", () => {
    expect(boardInterval(1)).toBe(2000);
    expect(boardInterval(0)).toBe(5000);
    expect(runInterval(true)).toBe(1000);
    expect(runInterval(false)).toBe(4000);  // keeps watching, so an evaluation shows up
  });
});

describe("toasts", () => {  // one toast per finished job, never for jobs that ended before the page opened
  it("announces each finished job once", () => {
    const seen = new Set<string>();
    const job = (id: string, extra: Partial<Job> = {}): Job =>
      ({ id, kind: "run", run_id: `r-${id}`, label: "", alive: false, exit_code: 0, failed: false, message: null, finished_at: 200, ...extra });
    const jobs = [job("a"), job("b", { alive: true, finished_at: null }), job("c", { finished_at: 50 }), job("d", { exit_code: 2, failed: true, message: "boom" })];
    const first = jobToasts(jobs, seen, 100);
    expect(first.map((t) => [t.id, t.tone])).toEqual([["a", "ok"], ["d", "bad"]]);
    expect(first[1].text).toContain("boom");
    expect(jobToasts(jobs, seen, 100)).toEqual([]);
  });
});

describe("rubric form", () => {  // U4: the API's field path puts the message next to its field
  it("maps field paths", () => {
    expect(fieldOf("criteria.2.levels")).toEqual({ index: 2, key: "levels" });
    expect(fieldOf("criteria.0")).toEqual({ index: 0, key: null });
    expect(fieldOf("rubric_id")).toEqual({ index: null, key: "rubric_id" });
    expect(fieldOf(null)).toEqual({ index: null, key: null });
  });

  it("shows the error inside the criterion it belongs to", () => {
    const body: Rubric = { rubric_id: "r", version: "1", description: "", criteria: [
      { id: "a", name: "A", description: "", max: 3, weight: 1, levels: { "1": "x", "3": "y" } },
      { id: "b", name: "B", description: "", max: 3, weight: 1, levels: { "1": "x" } }] };
    render(<RubricForm body={body} onChange={() => {}} error="criteria.1: levels must describe 3" field="criteria.1" />);
    const second = screen.getByRole("group", { name: "Criterion 2" });
    expect(within(second).getByRole("alert").textContent).toContain("levels must describe 3");
    expect(within(screen.getByRole("group", { name: "Criterion 1" })).queryByRole("alert")).toBeNull();
  });
});

describe("run drawer", () => {  // T2: the drawer's tabs show the monitor's views
  it("switches between overview, timeline, steps and report", async () => {
    const { RunDrawer } = await import("./pages/Runs");
    const { fireEvent, findByText } = await import("@testing-library/react");
    const step = { step: 1, type: "llm_call", name: "model", status: "ok", duration_ms: 1200, started_at: "x", text: "hi", text_html: "<p>Planning the build</p>", tokens_in: 10, tokens_out: 5 };
    const detail = {
      run: { run_id: "r1", prompt_id: "adb-001", prompt: "Map cropland", model: { id: "m" }, status: "succeeded" },
      scores: { overall: 0.9, workflow: { score: 1 }, output: { score: 0.8 } }, report_html: "<h1>Evaluation report</h1>",
      rubric_info: null, evaluating: false, interrupted: false,
      m: { live: false, status: "succeeded", elapsed: "00:02", start_ms: null, gauges: [], cost: 0.01, tokens_out: "5",
        tools_ok: 0, tools_err: 0, servers: [], rows: [{ step: 1, kind: "model", label: "model", error: false, ms: 1200, new: false, left: 0, width: 50 }],
        working: null, axis: [{ pct: 0, label: "0s" }], feed: [{ s: step, new: false, agent: null, divider: false }], seen: 1 },
    };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(detail), { status: 200 })));
    render(<MemoryRouter><RunDrawer id="r1" onEvaluate={() => {}} /></MemoryRouter>);
    expect(await findByText(document.body, "Map cropland")).toBeTruthy();
    expect(screen.getByRole("img", { name: "overall .90" })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Timeline" }));
    expect(screen.getByText("tool")).toBeTruthy();  // the waterfall legend
    fireEvent.click(screen.getByRole("tab", { name: /Steps/ }));
    expect(screen.getByText("Planning the build")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Report" }));
    expect(screen.getByRole("heading", { name: "Evaluation report" })).toBeTruthy();
    vi.unstubAllGlobals();
  });
});

describe("usePoll", () => {  // verify 2: a response that arrives after the drawer closed must not keep polling
  it("drops late responses and stops after unmount", async () => {
    const { renderHook } = await import("@testing-library/react");
    const { usePoll } = await import("./usePoll");
    let resolve: (r: Response) => void = () => {};
    const fetchMock = vi.fn(() => new Promise<Response>((r) => { resolve = r; }));
    vi.stubGlobal("fetch", fetchMock);
    vi.useFakeTimers();
    const { unmount } = renderHook(() => usePoll<{ n: number }>("/api/runs/x", () => 1000));
    unmount();  // the drawer closes while the first request is still in flight
    resolve(new Response(JSON.stringify({ n: 1 }), { status: 200 }));
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchMock).toHaveBeenCalledTimes(1);  // no further polls
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });
});
