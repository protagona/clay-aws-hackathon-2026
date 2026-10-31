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
    expect(within(attention).getByRole("button", { name: /Score it/ })).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "Being scored" })).getByText("None right now")).toBeTruthy();
    expect(screen.getByText("The judge is grading the result.")).toBeTruthy();  // increment 12, C4: every column has a caption
    expect(screen.getByText(/Workflow — · Output —/)).toBeTruthy();
  });

  it("offers Evaluate only on cards that need it", () => {
    const onEvaluate = vi.fn();
    render(<MemoryRouter><Board runs={[card("c", "attention", { headline: "not evaluated" }), card("u", "attention", { unreadable: true })]} onEvaluate={onEvaluate} /></MemoryRouter>);
    const buttons = screen.getAllByRole("button", { name: /Score it/ });
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
    for (const name of ["Scores", "Limits used", "Latest steps"]) expect(screen.getByRole("group", { name })).toBeTruthy();  // increment 12, C3
    fireEvent.click(screen.getByRole("tab", { name: "Timeline" }));
    expect(screen.getByText("tool call")).toBeTruthy();  // the waterfall legend
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

describe("RubricView", () => {  // increment 10, R3/R5: every level shown; the run's level and the judge's score marked
  it("renders levels and highlights the scored level", async () => {
    const { RubricView } = await import("./components/RubricView");
    const rubric: Rubric = { rubric_id: "r", version: "1", description: "Desc <b>x</b>", criteria: [
      { id: "fit", name: "Data fit", description: "Datasets fit", max: 4, weight: 2, levels: { "1": "Wrong", "2": "Weak", "3": "Good", "4": "Great" } }] };
    render(<RubricView rubric={rubric} scores={[{ id: "fit", score: 3, max: 4, rationale: "Mostly right" }]} />);
    const levels = screen.getByRole("list", { name: "Data fit levels" });
    expect(within(levels).getAllByRole("listitem")).toHaveLength(4);
    expect(within(levels).getByText(/Good/).closest("li")?.getAttribute("aria-current")).toBe("true");
    expect(screen.getByText("scored 3/4")).toBeTruthy();
    expect(screen.getByText("Mostly right")).toBeTruthy();  // the rationale is its own span (judge-written content)
    expect(screen.getByText("Desc <b>x</b>")).toBeTruthy();  // text, never HTML
  });
});

describe("key numbers", () => {  // increment 11, K1/K2: the KPI cards describe only the runs the filters show
  it("follow the model filter", async () => {
    const { default: Runs } = await import("./pages/Runs");
    const { fireEvent, findByText } = await import("@testing-library/react");
    const today = new Date().toISOString();
    const runs = [
      card("k1", "scored", { started_at: today, kpi: { spent: 0.1, overall: 0.9 } }),
      card("k2", "running", { started_at: "2026-01-01T00:00:00Z", kpi: { spent: 0.05, overall: null } }),
      card("g1", "scored", { model: "zai.glm-5", started_at: today, kpi: { spent: 1, overall: 0.5 } }),
    ];
    const kpis = { runs: 3, today: 2, mean: 0.7, scored: 2, spent: 1.15, live: 1, running: 1, evaluating: 0 };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ runs, live: 1, kpis }), { status: 200 })));
    render(<MemoryRouter><Runs /></MemoryRouter>);
    const box = await screen.findByRole("group", { name: "Key numbers" });
    expect(await findByText(box, "$1.15")).toBeTruthy();  // unfiltered: the server's totals
    expect(within(box).getByText("0.70")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "us.moonshotai.kimi-k3" } });
    expect(within(box).getByText("$0.15")).toBeTruthy();
    expect(within(box).getByText("0.90")).toBeTruthy();
    expect(within(box).getByText(/of 3/)).toBeTruthy();
    expect(within(box).getByText("+1 today")).toBeTruthy();
    expect(within(box).getByText("1 running · 0 being scored")).toBeTruthy();
    expect(within(box).getByText("1 scored · active rubric")).toBeTruthy();
    vi.unstubAllGlobals();
  });
});

describe("clarity (increment 12)", () => {
  it("Section is a labelled section with its number, heading and caption", async () => {
    const { Section, PageHeader } = await import("./components/ui");
    render(<><PageHeader title="Runs" caption="Every agent run." /><Section n={2} title="Runs by status" caption="Filter to narrow."><p>body</p></Section></>);
    expect(screen.getByRole("heading", { level: 1, name: "Runs" })).toBeTruthy();
    const sec = screen.getByRole("region", { name: "Runs by status" });
    expect(within(sec).getByRole("heading", { level: 2 }).textContent).toBe("Runs by status");
    expect(within(sec).getByText("02")).toBeTruthy();
    expect(within(sec).getByText("Filter to narrow.").hasAttribute("data-caption")).toBe(true);
  });

  it("formats shorthand as plain words", async () => {
    const f = await import("./format");
    expect(f.stepKind("llm_call")).toBe("model call");
    expect(f.stepKind("agent_step")).toBe("agent");
    expect(f.statusWord("not evaluated")).toBe("not scored yet");
    expect(f.statusWord("timeout")).toBe("timed out");
    expect(f.seconds(1918)).toBe("1.9 s");
    expect(f.score2("0.9584")).toBe("0.96");
    expect(f.score2("")).toBe("—");
    expect(f.pretty("data-fit")).toBe("Data fit");
    expect(f.pretty("completion")).toBe("Completion");
    expect(f.pretty("Overall")).toBe("Overall");
    expect(f.pretty("adb-005 · stage 3 (get_dataset_details|preview_dataset|sample_dataset)"))
      .toBe("adb-005 · stage 3 (get_dataset_details, preview_dataset or sample_dataset)");
  });

  it("colours a difference by which way is better", async () => {  // C6: a cost increase is red, not green
    const { deltaTone } = await import("./format");
    expect(deltaTone("+$0.172", "lower")).toBe("bad");
    expect(deltaTone("−0.03", "higher")).toBe("bad");
    expect(deltaTone("+0.05", "higher")).toBe("good");
    expect(deltaTone("−4 s", "lower")).toBe("good");
    expect(deltaTone("+4", null)).toBe("none");
    expect(deltaTone("0", "higher")).toBe("none");
  });

  it("offers Score this run on an unscored run's overview", async () => {  // C3
    const { RunDrawer } = await import("./pages/Runs");
    const { findByText } = await import("@testing-library/react");
    const detail = {
      run: { run_id: "r2", prompt_id: "adb-001", prompt: "Map cropland", model: { id: "m" }, status: "succeeded" },
      scores: null, report_html: null, rubric_info: null, evaluating: false, interrupted: false,
      m: { live: false, status: "succeeded", elapsed: "00:02", start_ms: null, gauges: [], cost: 0.01, tokens_out: "5",
        tools_ok: 2, tools_err: 1, servers: [], rows: [], working: null, axis: [], feed: [], seen: 0 },
    };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(detail), { status: 200 })));
    const onEvaluate = vi.fn();
    render(<MemoryRouter><RunDrawer id="r2" onEvaluate={onEvaluate} /></MemoryRouter>);
    expect(await findByText(document.body, "Not scored yet.")).toBeTruthy();
    expect(screen.getByText(/3 tool calls/)).toBeTruthy();
    screen.getByRole("button", { name: "Score this run" }).click();
    expect(onEvaluate).toHaveBeenCalledWith("r2");
    vi.unstubAllGlobals();
  });
});

describe("run panel score states (increment 12, C3)", () => {
  const base = {
    run: { run_id: "r3", prompt_id: "adb-001", prompt: "Map cropland", model: { id: "m" }, status: "succeeded" },
    scores: null, report_html: null, rubric_info: null, evaluating: false, interrupted: false,
    m: { live: false, status: "succeeded", elapsed: "00:02", start_ms: null, gauges: [], cost: 0.01, tokens_out: "5",
      tools_ok: 0, tools_err: 0, servers: [], rows: [], working: null, axis: [], feed: [], seen: 0 },
  };
  async function open(detail: object) {
    const { RunDrawer } = await import("./pages/Runs");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(detail), { status: 200 })));
    render(<MemoryRouter><RunDrawer id="r3" onEvaluate={() => {}} /></MemoryRouter>);
    await screen.findByText("Map cropland");
  }
  afterEach(() => vi.unstubAllGlobals());
  it("says Being scored while the judge works", async () => {
    await open({ ...base, evaluating: true });
    expect(screen.getByText(/Being scored…/)).toBeTruthy();
    expect(screen.getByRole("status", { name: "Being scored" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Score this run" })).toBeNull();
  });
  it("offers to score again after an interrupted scoring", async () => {
    await open({ ...base, interrupted: true });
    expect(screen.getByText("Scoring was interrupted.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Score this run" })).toBeTruthy();
  });
  it("marks a partly scored run", async () => {
    await open({ ...base, scores: { overall: null, workflow: { score: 1 }, output: { score: null } } });
    expect(screen.getByText(/Partly scored/)).toBeTruthy();
  });
});
