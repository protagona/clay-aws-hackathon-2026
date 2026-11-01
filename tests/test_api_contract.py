"""The API contract (increment 17): every route declares its response, responses are checked against it, the web
app's types are generated from it and used where they belong, and request bodies are strict."""

import importlib.util
import json
import re
import shutil

import pytest
from conftest import FIXTURES, ROOT
from eval_helpers import eval_config, load
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from foundry.engine.evaluate.evaluate import evaluate
from foundry.server import create_app
from foundry.services import batches as batch_service

BASE = "http://127.0.0.1:8765"
spec = importlib.util.spec_from_file_location("gen_api_types", ROOT / "scripts" / "gen_api_types.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

# Routes that don't return a JSON model: a file download, and the catch-all that only ever raises 404.
EXEMPT = {("GET", "/api/rubric/drafts/{draft_id}/source"), ("GET", "/api/{rest:path}"), ("POST", "/api/{rest:path}"),
          ("PUT", "/api/{rest:path}"), ("DELETE", "/api/{rest:path}"), ("PATCH", "/api/{rest:path}")}


def _client(tmp_path):
    c = TestClient(create_app(tmp_path / "runs", ROOT, port=8765), base_url=BASE, follow_redirects=False)
    return c, {"x-csrf-token": c.get("/api/session").json()["csrf"]}


# --- R1: every route declares its response ----------------------------------------------------------------------


def test_every_json_route_declares_its_response(tmp_path):
    from foundry.api.routes import make_router
    from foundry.services.workspace import Workspace

    seen, missing = set(), []
    for r in make_router(Workspace(tmp_path, ROOT, 8765), "token", access=object()).routes:
        if isinstance(r, APIRoute) and r.path.startswith("/api/"):
            for m in r.methods:
                seen.add((m, r.path))
                if r.response_model is None and (m, r.path) not in EXEMPT:
                    missing.append(f"{m} {r.path}")
                if r.response_model is not None and not r.response_model_exclude_unset:
                    missing.append(f"{m} {r.path} (without exclude_unset: absent keys would become null)")
    assert not missing, missing
    assert EXEMPT <= seen, f"exempt routes that no longer exist: {EXEMPT - seen}"
    assert len(seen) - len(EXEMPT) >= 30


# --- R2: a response that breaks its model is a 500 --------------------------------------------------------------


def test_response_drift_is_a_500(tmp_path, monkeypatch, caplog):
    from foundry.services import imports

    c, _ = _client(tmp_path)
    assert c.get("/api/receiver").status_code == 200
    monkeypatch.setattr(imports, "receiver_status", lambda ws: {"running": False, "port": 1, "endpoint": "x",
                                                                "count": 0, "received": [], "renamed": 1})
    with caplog.at_level("ERROR", logger="foundry.api"):
        r = c.get("/api/receiver")
    assert r.status_code == 500 and r.json() == {"error": "Internal Server Error"}
    assert "GET /api/receiver: response.renamed extra_forbidden" in caplog.text  # where it broke, for the log
    monkeypatch.setattr(imports, "receiver_status", lambda ws: {"running": "maybe", "port": 1, "endpoint": "x",
                                                                "count": 0, "received": []})
    assert c.get("/api/receiver").status_code == 500


# --- R4: untrusted files never make a route 500 -----------------------------------------------------------------


def _hostile_runs(runs):
    runs.mkdir(parents=True)
    for name in ("clean", "hostile", "failed"):
        shutil.copytree(FIXTURES / name, runs / f"fixture-{name}")
    base = json.loads((runs / "fixture-clean" / "run.json").read_text())
    bad = {"model-id": {"model": {**base["model"], "id": {"x": 1}}}, "prompt": {"prompt": ["x"]},
           "task": {"task_id": 7}, "agents": {"agents": "pipeline"}, "agents-mode": {"agents": {"mode": 3}},
           "started": {"started_at": 12}}
    for name, patch in bad.items():
        shutil.copytree(FIXTURES / "clean", runs / f"bad-{name}")
        (runs / f"bad-{name}" / "run.json").write_text(json.dumps({**base, **patch}))
    return list(bad)


def test_hostile_history_never_500(tmp_path):
    root = tmp_path / "proj"
    shutil.copytree(ROOT / "tasks", root / "tasks", ignore=shutil.ignore_patterns("drafts", "sources"))
    shutil.copytree(ROOT / "config", root / "config")
    hist = root / "tasks" / "adb" / "rubrics" / "history.jsonl"
    hist.parent.mkdir(parents=True, exist_ok=True)
    good = {"rubric_id": "r", "version": "1", "sha256": "a" * 64, "file": "r@1.json", "activated_at": "x"}
    lines = [good, {**good, "version": "2", "file": "r@2.json", "activated_at": None},
             {k: v for k, v in good.items() if k != "activated_at"} | {"version": "3", "file": "r@3.json"},
             {**good, "file": "r@4.json", "source": {"kind": 5}}, {**good, "file": "r@5.json", "rubric_id": 7},
             {**good, "file": "r@6.json", "note": ["x"]}]
    hist.write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
    c = TestClient(create_app(tmp_path / "runs", root, port=8765), base_url=BASE)
    r = c.get("/api/rubric")
    assert r.status_code == 200, r.text
    assert {v["file"] for v in r.json()["versions"]} <= {"r@1.json", "r@2.json", "r@3.json"}


def test_hostile_files_never_500(tmp_path):
    runs = tmp_path / "runs"
    bad = _hostile_runs(runs)
    evaluate(runs / "fixture-clean", load(eval_config(tmp_path / "e")))
    sc = json.loads((runs / "fixture-clean" / "scores.json").read_text())
    sc["output"]["criteria"] = [{"id": "ok", "score": 3, "max": "four", "name": 5}, {"id": 1, "score": 2},
                                {"id": "s", "score": "3"}, "nope", {"id": "b", "score": True}]
    sc["workflow"]["cost_usd"] = True
    (runs / "fixture-clean" / "scores.json").write_text(json.dumps(sc))
    batch = runs / ".batches" / "b-20261031-000000-abcd"
    batch.mkdir(parents=True)
    (batch / "batch.json").write_text(json.dumps({"status": 5, "jobs": "many", "max_cost": "x", "spent_usd": [],
                                                  "models": "m", "configs": [1]}))
    (batch / "summary.csv").write_text("variant,status,extra\nv1,succeeded,a,b,c\n,\n")
    shutil.copytree(FIXTURES / "clean", runs / "nan-score")
    evaluate(runs / "nan-score", load(eval_config(tmp_path / "e2")))
    nan = json.loads((runs / "nan-score" / "scores.json").read_text()) | {"overall": float("nan")}
    (runs / "nan-score" / "scores.json").write_text(json.dumps(nan))  # Python writes the token NaN
    c, _ = _client(tmp_path)
    board = c.get("/api/runs").json()
    assert next(r for r in board["runs"] if r["run_id"] == "nan-score")["overall"] is None  # NaN is not a score
    unreadable = {r["run_id"] for r in board["runs"] if r.get("unreadable")}
    assert unreadable == {f"bad-{n}" for n in bad}
    clean = next(r for r in board["runs"] if r["run_id"] == "fixture-clean")
    assert clean["cost"] != True  # noqa: E712 (a bool cost_usd is not a cost)
    detail = c.get("/api/runs/fixture-clean").json()
    assert detail["scores"]["output"]["criteria"] == [{"id": "ok", "score": 3}]
    paths = ["/api/session", "/api/runs", "/api/runs/fixture-clean", "/api/runs/fixture-hostile",
             "/api/runs/fixture-failed", "/api/runs/fixture-clean/rubric", "/api/dashboard", "/api/dashboard?rubric=all",
             "/api/batches", f"/api/batches/{batch.name}", "/api/prompts", "/api/prompts/adb-001",
             "/api/rubric", "/api/rubric/active", "/api/receiver", "/api/jobs",
             "/api/compare?a=fixture-clean&b=fixture-hostile"]
    for p in paths:
        r = c.get(p)
        assert r.status_code == 200, (p, r.text[:300])
    b = c.get(f"/api/batches/{batch.name}").json()
    assert b["meta"] == {}  # every wrong-typed field dropped
    assert all(isinstance(row["variant"], str) and isinstance(row["prompt"], str) for row in b["rows"])
    listed = c.get("/api/batches").json()["batches"][0]
    assert listed["models"] == [] and listed["max_cost"] is None and isinstance(listed["jobs"], int)


# --- T1, T2: the generated types are current --------------------------------------------------------------------


def test_types_ts_is_current():
    current = (ROOT / "web" / "src" / "types.ts").read_text()
    assert gen.render() == current, "web/src/types.ts is stale; run: uv run python scripts/gen_api_types.py"


def test_generator_rejects_what_it_cannot_express():
    with pytest.raises(ValueError):
        gen.ts_type({"type": "object", "properties": {"a": {"type": "string"}}})
    with pytest.raises(ValueError):
        gen.ts_type({"oneOf": [{"type": "string"}]})


# --- T3: call sites name the type their route declares ----------------------------------------------------------

HELPERS = re.compile(r"\b(usePoll|get|post|put|del|upload)\s*(<)?")
METHOD = {"usePoll": "GET", "get": "GET", "post": "POST", "put": "PUT", "del": "DELETE", "upload": "POST"}
READS = {"usePoll", "get"}
# Helpers' own generic plumbing (`get<T>(url)` inside usePoll and api.ts), not call sites.
PLUMBING = {"api.ts", "usePoll.ts"}
# Calls whose URL is a variable: file -> the route types they may name. A new one fails until it is listed here.
DYNAMIC = {"RubricVersion.tsx": {"RubricWithMeta", "RubricVersion"}}
LITERAL = re.compile(r"\"([^\"]*)\"|'([^']*)'|`([^`]*)`")


def _balanced(text: str, i: int, open_: str, close: str) -> int:
    """Index just past the bracket that closes the one at text[i] (strings and template literals skipped)."""
    depth, quote = 0, None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch in open_:
            depth += 1
        elif ch in close:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced")


def _first_arg(text: str, i: int) -> str:
    """The first argument of the call whose "(" is at text[i]."""
    depth, quote, j = 0, None, i + 1
    while j < len(text):
        ch = text[j]
        if quote:
            if ch == "\\":
                j += 1
            elif ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif ch == "," and depth == 0:
            break
        j += 1
    return text[i + 1:j].strip()


def call_sites(text: str):
    """(helper, type parameter text or None, first argument) for each call of an api helper."""
    for m in HELPERS.finditer(text):
        helper, i = m.group(1), m.end()
        typ = None
        if m.group(2):
            close = _balanced(text, i - 1, "<{([", ">})]")
            typ, i = text[i:close - 1].strip(), close
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text) or text[i] != "(":
            continue  # a name, not a call (an import, a declaration's type, `params.get` without a paren...)
        yield helper, typ, _first_arg(text, i)


def check_call_sites(files, routes) -> tuple[list[str], list[str]]:
    def route_type(method, url):
        path = re.sub(r"\$\{[^}]*\}", "X", url).split("?")[0]
        return next((name for m, pat, name in routes if m == method and pat.match(path)), None)

    found, wrong = [], []
    for name, text in files:
        # `import type { Compare as CompareT }`: a page named like its type imports it under another name.
        alias = dict((b, a) for a, b in re.findall(r"\b(\w+) as (\w+)\b", " ".join(
            re.findall(r"import type \{([^}]*)\} from \"[./]*types\"", text))))
        for helper, typ, arg in call_sites(text):
            if typ is not None and not typ.startswith("{"):
                typ = " | ".join(alias.get(t.strip(), t.strip()) for t in typ.split("|"))
            urls = ["".join(groups) for groups in LITERAL.findall(arg)]
            api = [u for u in urls if u.startswith("/api/")]
            where = f"{name}: {helper}<{typ}>({arg[:60]})"
            if not api:
                # No /api literal: an api call only if it names a type (other .get()s, Map.get(k), don't). Then it
                # is a variable or computed URL (a variable, a call, a concatenation, `${BASE}/api/...`).
                if name in PLUMBING or typ is None:
                    continue
                allowed = DYNAMIC.get(name)
                if allowed is None:
                    wrong.append(f"{where}: a variable URL; list it in DYNAMIC with the types it may name")
                elif not {t.strip() for t in typ.split("|")} <= allowed:
                    wrong.append(f"{where}: allowed types here are {sorted(allowed)}")
                else:
                    found.append(where)
                continue
            if typ is not None and typ.startswith("{"):
                wrong.append(f"{where}: an inline type; use the generated type")
                continue
            if typ is None and helper in READS:
                wrong.append(f"{where}: a read must name its type")
                continue
            for url in api:
                expected = route_type(METHOD[helper], url)
                found.append(f"{name}: {METHOD[helper]} {url}")
                if expected is None:
                    wrong.append(f"{where}: {METHOD[helper]} {url} matches no route")
                elif typ is not None and typ != expected:
                    wrong.append(f"{where}: names {typ} but {METHOD[helper]} {url} returns {expected}")
    return found, wrong


def _routes():
    return [(m, _route_pattern(p), model.__name__) for m, p, model, _ in gen.api_routes() if model is not None]


def _route_pattern(path: str) -> re.Pattern:
    return re.compile("^" + re.sub(r"\\\{[^}]*\\\}", "[^/]+", re.escape(path)) + "$")


def test_call_sites_use_the_route_type():
    files = [(f.name, f.read_text()) for f in (ROOT / "web" / "src").rglob("*.ts*")
             if ".test." not in f.name and f.name != "types.ts"]
    found, wrong = check_call_sites(files, _routes())
    assert not wrong, "\n".join(wrong)
    assert len(found) >= 15, found  # not vacuous


def test_call_site_check_catches_each_mistake():
    routes = _routes()
    bad = {"inline": 'usePoll<{ jobs: Job[] }>("/api/jobs")', "untyped read": 'get("/api/jobs")',
           "wrong type": 'usePoll<RunDetail>("/api/runs")', "ternary branch": 'usePoll<Compare>(x ? "/api/runs" : null)',
           "first match": 'usePoll<PromptDetail>("/api/prompts/tools?task=adb")',
           "unlisted variable": "usePoll<Compare>(url)", "no route": 'usePoll<Ok>("/api/nope/1")',
           "single quotes": "usePoll<Card>('/api/runs')", "computed": "usePoll<RunsResponse>(urlFor(id))",
           "prefixed": "usePoll<RunsResponse>(`${BASE}/api/runs`)", "concatenated": 'usePoll<RunsResponse>("/api" + "/runs")'}
    for label, code in bad.items():
        _, wrong = check_call_sites([("X.tsx", code)], routes)
        assert wrong, label
    good = ['usePoll<ToolMenu>("/api/prompts/tools?task=adb")', 'post("/api/receiver/stop")',
            'usePoll<Compare>(a ? `/api/compare?a=${a}` : null)', 'post<StartRunResponse>("/api/runs", {})']
    for code in good:
        found, wrong = check_call_sites([("X.tsx", code)], routes)
        assert found and not wrong, (code, wrong)


# --- Q1, Q2: strict request bodies ------------------------------------------------------------------------------

BODIES = [("post", "/api/runs", {"prompt_id": "adb-001", "config": "offline"}),
          ("post", "/api/batches", {"config": "offline", "prompts": ["adb-001"]}),
          ("post", "/api/receiver/start", {}),
          ("put", "/api/rubric/drafts/x", {"body": {}}),
          ("post", "/api/prompts", {}),
          ("put", "/api/prompts/adb-001", {}),
          ("post", "/api/prompts/adb-001/version", {}),
          ("post", "/api/prompts/adb-001/archive", {"archived": False}),
          ("put", "/api/prompts/adb-001/stages", {"stages": []}),
          ("post", "/api/rubric/history/activate", {"file": "x"})]


@pytest.mark.parametrize(("method", "path", "body"), BODIES)
def test_unknown_body_field_is_422(tmp_path, method, path, body):
    c, h = _client(tmp_path)
    r = getattr(c, method)(path, headers=h, json={**body, "surprise": 1})
    assert r.status_code == 422 and r.json() == {"error": "surprise: Extra inputs are not permitted"}


def test_batch_ranges_are_422(tmp_path):
    c, h = _client(tmp_path)
    base = {"config": "offline", "prompts": ["adb-001"]}
    lo_r, hi_r = batch_service.REPEATS
    lo_c, hi_c = batch_service.CONCURRENCY
    for field, value in (("repeats", lo_r - 1), ("repeats", hi_r + 1), ("concurrency", lo_c - 1),
                         ("concurrency", hi_c + 1), ("max_cost", 0), ("max_cost", batch_service.MAX_COST + 1)):
        r = c.post("/api/batches", headers=h, json={**base, field: value})
        assert r.status_code == 422 and r.json()["error"].startswith(f"{field}: "), (field, value, r.text)
