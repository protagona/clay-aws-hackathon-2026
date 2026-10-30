# Visuals

Put these on the screen at kickoff and come back to them at every checkpoint.

## 1. The loop (what we're building)

```mermaid
flowchart LR
    T["Task + prompt"] --> A["Agent<br/>(Bedrock)"]
    A <--> M["Geospatial tools<br/>(MCP)"]
    A --> R["Record<br/>trace"]
    R --> S["Summarize"]
    S --> G["Score<br/>workflow + output"]
    G --> P["Report"]
    R -. "saved runs" .-> X["Replay"]
    X --> S
```

**Observe → summarize → score.** Everything we build sits on one of these arrows.

## 2. Architecture and swap points

```mermaid
flowchart TB
    subgraph Inputs
        TP["Task pack<br/>prompts + rubric + refs"]
    end

    subgraph Data["Public data (us-west-2)"]
        OD["Open Data on AWS<br/>NAIP (Requester-Pays), Vantor"]
        PS["Pre-staged sample<br/>(our bucket)"]
        EMB["LGND embeddings<br/>(caption task)"]
    end

    subgraph Harness["Agent harness (Python)"]
        AG["Agent loop"]
        MA["Model adapter"]
        TA["Tracer adapter"]
        MC["MCP client"]
    end

    subgraph Tools["MCP servers"]
        RODA["RODA MCP"]
        CL["Clay shortlist"]
    end

    subgraph Eval["Evaluation (Python)"]
        SUM["Summarizer"]
        WS["Workflow scorer<br/>(rules on trace)"]
        OS["Output scorer<br/>(LLM judge vs rubric)"]
        REP["Report builder"]
    end

    BR[("Bedrock")]
    OT[("Optional tracing backend<br/>e.g. OTel / Langfuse")]
    ST[("runs/ → S3")]
    UI["Thin UI / CLI"]

    TP --> AG
    AG --> MA --> BR
    AG --> MC --> RODA & CL
    RODA --> OD
    CL --> PS & EMB
    AG --> TA --> ST
    TA -.-> OT
    ST --> SUM & WS & OS
    SUM & WS & OS --> REP --> ST
    UI --> AG
    UI --> ST
```

- The **adapters** (model and tracer) are where M6 "swappable" lives.
- The summarizer and output scorer also call Bedrock **through the model adapter**.
- If Clay supplies the harness under test, it replaces the "Agent harness" box. Everything below `runs/` stays the same.
- `runs/` is the contract boundary; see [trace-contract.md](trace-contract.md).
- Which tool reaches which data is illustrative. RODA does discovery and sampling of Open Data on AWS; the Clay shortlist is unknown until kickoff.

## 3. One run, step by step

```mermaid
sequenceDiagram
    actor U as User / UI
    participant H as Harness
    participant B as Bedrock
    participant M as MCP server
    participant T as Tracer
    participant E as Evaluator

    U->>H: run(task, prompt, model, tracer)
    H->>T: start run.json
    loop until done or step limit
        H->>B: reason / plan
        T-->>T: llm_call step
        H->>M: call tool(args)
        M-->>H: result or error
        T-->>T: tool_call step
    end
    H->>T: output.json, close run
    U->>E: evaluate(run_id)
    E->>E: summarize + score workflow + score output
    E-->>U: report.md
```

Replay is the same thing with the top half skipped: `evaluate(run_id)` on a saved run.

## 4. Who can work on what, and when

```mermaid
flowchart LR
    K5["Pick framework<br/>+ IaC (K-5)"] --> K2["Repo skeleton<br/>(K-2)"]
    C(("Trace contract<br/>+ fixtures<br/>FIRST 90 MIN"))
    RB["Rubric + prompts<br/>(E-1, E-2)<br/>right after kickoff"]

    K2 --> IAC["IaC + Bedrock<br/>access (A-1)"]
    K2 --> MAD["Model adapter<br/>interface (B-1)"]
    K2 --> MCP["MCP client<br/>RODA (B-2)"]
    MAD --> H["Agent loop<br/>(B-3)"]
    MCP --> H

    C --> TR["Tracer<br/>(C-1)"]
    C --> WS["Workflow<br/>scorer (D-1)"]
    MAD --> SU["Summarizer<br/>(D-2)"]
    MAD --> OS["Output<br/>scorer (D-3)"]
    C --> SU & OS
    RB --> OS
    C --> UI["UI (F-1)<br/>Should"]

    WS & SU & OS --> REP["Report + replay<br/>CLI (D-4, D-5)"]
    H & TR --> LT["Live trace<br/>(C-2) CP-2"]
    IAC & TR --> S3["Mirror to<br/>S3 (C-3)"]
    LT & REP & S3 --> E2E{{"One command<br/>end to end<br/>(D-8) CP-4"}}
    UI -.-> E2E
```

What blocks others, all of it small and early:

- **The contract and fixtures** (the first ~90 minutes, everyone). They unblock the tracer, all the scorers and the UI.
- **Picking the framework and IaC, then the repo skeleton.** These unblock the AWS setup, the model adapter and the MCP client.
- **The model adapter interface.** The agent, the summarizer and the judge all call the LLM through it.

Nothing waits on Clay's answers: the rubric work starts right after kickoff with a stand-in. Everyone builds in their own sandbox until the shared one is up. The UI is a Should, so the end-to-end milestone doesn't wait for it.

## 5. Timeline

The docs call this a **2-day build** but give no schedule. These times are assumed; adjust them at kickoff to the real agenda.

```mermaid
gantt
    title Day 1 — get one trace through the whole loop
    dateFormat HH:mm
    axisFormat %H:%M
    section Everyone
    Kickoff, confirm unknowns with Clay      :crit, k, 09:00, 45m
    Ratify trace contract + fixtures         :crit, c, after k, 45m
    Pick framework + repo skeleton (K-5, K-2) :rs, after k, 45m
    Checkpoint (CP-1)                        :milestone, after c, 0m
    section Build
    IaC + Bedrock access (A-1)               :i, after rs, 2h
    Mirror to S3 + stage data (C-3, A-2)     :i2, after i, 2h
    Harness + RODA MCP (B-1..B-3)            :h, after rs, 210m
    Wire tracer into harness (C-2)           :cw, after h, 30m
    Tracer adapter (C-1)                     :t, after c, 3h
    Summarizer on fixtures (D-2)             :s, after c, 3h
    Workflow scorer on fixtures (D-1)        :w, after c, 3h
    Rubric + synthetic prompts (E-1, E-2)    :r, after k, 2h
    Output scorer, LLM judge (D-3)           :o, after r, 3h
    Report + replay CLI (D-4, D-5)           :rp, after o, 90m
    section Integrate
    First live trace captured (CP-2)         :milestone, 14:30, 0m
    Report from fixture (CP-3)               :milestone, 16:30, 0m
    Stand-up + re-plan                       :crit, 17:00, 30m
```

```mermaid
gantt
    title Day 2 — make it real, make it reliable, show it
    dateFormat HH:mm
    axisFormat %H:%M
    section Must
    One command end to end, D-8 (CP-4)        :crit, d, 09:00, 3h
    Swap model + tracer (B-4, C-4)           :sw, 09:00, 2h
    Record golden runs for replay            :g, after d, 1h
    Deploy from clean checkout (A-4)         :dc, 11:00, 3h
    section Should / Could
    Thin UI wired to runs                    :u, 09:00, 4h
    Second task OR sensitivity analysis      :x, 11:00, 3h
    section Ship
    Feature freeze (CP-5)                     :milestone, 14:00, 0m
    README polish (G-2)                      :doc, 14:00, 90m
    Demo rehearsal x2                        :crit, demo, 15:30, 1h
```

## 6. Checkpoint board (are we on track?)

| Milestone | When (assumed) | Done when | ✅ |
|-----------|----------------|-----------|----|
| **CP-1** Contract ratified | D1 ~10:30 | `trace-contract.md` agreed; 3 fixture runs committed | ☐ |
| **CP-2** First live trace | D1 ~14:30 | Agent calls RODA via MCP on Bedrock; `trace.jsonl` written | ☐ |
| **CP-3** First report | D1 ~16:30 | `report.md` generated for a fixture, with the summary and both scores (D-4) | ☐ |
| **CP-4** Live end to end | D2 ~12:00 | One command (D-8): live run → report, saved to S3 | ☐ |
| **CP-5** Freeze | D2 ~14:00 | Model **and** tracer swap demos work; the replay demo works; deploy from a clean checkout works; the repo is public (G-4) | ☐ |

**Rule:** if a milestone slips by more than an hour, cut the Could items first, then the Should items (the UI), before touching any Must.

## 7. "Should I work on this?"

```mermaid
flowchart TD
    Q["New idea / task"] --> M{"Does it move a<br/>Must (M1–M8)?"}
    M -- yes --> DO["Do it"]
    M -- no --> G{"Are all Musts green<br/>(or owned and on track)?"}
    G -- no --> HELP["Park it in the backlog.<br/>Help unblock a Must."]
    G -- yes --> S{"Is it on the<br/>Should NOT list?"}
    S -- yes --> NO["Don't."]
    S -- no --> T{"A new task<br/>(open track)?"}
    T -- yes --> CL["Get Clay's OK first"]
    T -- no --> OK["Pick the top<br/>Should / Could item"]
```
