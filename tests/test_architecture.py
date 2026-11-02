"""The layers of src/foundry and the rules between them (increment 16; docs/architecture.md).

    kernel    config, contract                  settings and run-record schemas; any layer may import them
    data      foundry.data.*                    files only: safe reads and writes
    engine    foundry.engine.*                  agents, models, MCP servers, tracing, scoring, batch, import
    services  foundry.services.*                what the app does
    api       foundry.api.*                     HTTP only
    ui        foundry.ui.*                      serves the built web app
    entry     foundry.cli, cli_rubric,          wire the layers together; may import anything
              __main__, server

Dependencies point down only. The checks below parse every module with `ast` (imports inside functions count too).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

KERNEL = {"foundry", "foundry.config", "foundry.contract"}
ENTRY = {"foundry.cli", "foundry.cli_rubric", "foundry.__main__", "foundry.server", "foundry.lambda_api", "foundry.cloud_boot", "foundry.lambda_worker", "foundry.lambda_probe",
         "foundry.cli_remote"}
LAYERS = ("data", "engine", "services", "api", "ui")
ALLOWED = {  # importer layer -> layers it may import (besides itself)
    "kernel": {"kernel"},
    "data": {"kernel", "data"},
    "engine": {"kernel", "data", "engine"},
    "services": {"kernel", "data", "engine", "services"},
    "api": {"kernel", "services", "api"},
    "ui": set(),
    "entry": {"kernel", "data", "engine", "services", "api", "ui", "entry"},
}
WEB_FRAMEWORKS = ("fastapi", "starlette")
WEB_LAYERS = {"api", "ui", "entry"}

# Modules not yet moved into their layer, and the layer each is headed for. Each slice of increment 16 shrinks this
# list; it must be empty when the increment is done (test_unmoved_modules_list_is_current keeps it honest).
UNMOVED: dict[str, str] = {}


def layer_of(module: str) -> str | None:
    if module in KERNEL:
        return "kernel"
    if module in ENTRY:
        return "entry"
    for name in LAYERS:
        if module == f"foundry.{name}" or module.startswith(f"foundry.{name}."):
            return name
    return None


def unmoved(module: str) -> str | None:
    for prefix, target in UNMOVED.items():
        if module == prefix or module.startswith(prefix + "."):
            return prefix
    return None


def module_name(path: Path, src: Path) -> str:
    parts = list(path.relative_to(src).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imports(path: Path, src: Path) -> list[tuple[int, str, str | None]]:
    """Every foundry module (and web framework) this file imports, with the line and, for `from m import name` of a
    name that isn't a module, that name. `from foundry import x` resolves to foundry.x when x is a module; relative
    imports are resolved against the file's package."""
    tree = ast.parse(path.read_text(), filename=str(path))
    me = module_name(path, src)
    package = me if path.name == "__init__.py" else me.rpartition(".")[0]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(node.lineno, a.name, None) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".")
                anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join(anchor + ([base] if base else []))
            for a in node.names:
                full = f"{base}.{a.name}"
                is_module = (src / Path(*full.split("."))).with_suffix(".py").exists() or (src / Path(*full.split("."))).is_dir()
                out.append((node.lineno, full, None) if is_module else (node.lineno, base, a.name))
    return out


def violations(src: Path, unmoved_ok: bool = True) -> list[str]:
    found = []
    for path in sorted(src.joinpath("foundry").rglob("*.py")):
        if "web_dist" in path.parts or "__pycache__" in path.parts:
            continue
        me = module_name(path, src)
        if unmoved_ok and unmoved(me):
            continue
        mine = layer_of(me)
        if mine is None:
            found.append(f"{path.relative_to(src)}: module {me} is in no layer")
            continue
        for line, target, name in imports(path, src):
            where = f"{path.relative_to(src)}:{line}"
            top = target.split(".")[0]
            if top in WEB_FRAMEWORKS and mine not in WEB_LAYERS:
                found.append(f"{where} imports {target}: only api, ui and the entry points may use {top}")
            if top in ("jinja2", "markupsafe"):
                found.append(f"{where} imports {target}: server-rendered templates were removed (increment 16)")
            if top != "foundry":
                continue
            if unmoved_ok and unmoved(target):
                continue  # an edge into a module that hasn't moved yet: judged once it has
            theirs = layer_of(target)
            if theirs is None:
                found.append(f"{where} imports {target}, which is in no layer")
            elif theirs not in ALLOWED[mine] | {mine}:
                found.append(f"{where} imports {target}: {mine} may import {', '.join(sorted(ALLOWED[mine]))}")
            elif theirs != mine and any(part.startswith("_") and not part.startswith("__")
                                        for part in [*target.split(".")[1:], name or ""]):
                found.append(f"{where} imports {target}{'.' + name if name else ''}: a private name of another layer")
    return found


def test_layers():
    problems = violations(SRC)
    assert not problems, "\n".join(problems)


def test_every_module_has_moved():
    assert not UNMOVED, "increment 16 is done only when every module is in its layer"


def test_unmoved_modules_list_is_current():
    """Every UNMOVED entry still exists (so the list shrinks as modules move, and can't go stale)."""
    gone = [m for m in UNMOVED if not ((SRC / Path(*m.split("."))).with_suffix(".py").exists()
                                       or (SRC / Path(*m.split("."))).is_dir())]
    assert not gone, f"moved already; remove from UNMOVED: {gone}"


def test_the_checker_catches_a_bad_edge(tmp_path):
    """The checker isn't a no-op: a data module importing the engine, an unclassified module, and a web framework
    outside the web layers are each reported."""
    pkg = tmp_path / "foundry"
    for d in ("", "data", "engine"):
        (pkg / d).mkdir(parents=True, exist_ok=True)
        (pkg / d / "__init__.py").write_text("")
    (pkg / "engine" / "runner.py").write_text("X = 1\n")
    (pkg / "data" / "bad.py").write_text("def f():\n    from foundry.engine import runner\n    import fastapi\n")
    (pkg / "data" / "store.py").write_text("def _atomic_write():\n    pass\n")
    (pkg / "engine" / "sneaky.py").write_text("from foundry.data.store import _atomic_write\nimport jinja2\n"
                                              "from foundry.data import store\n")  # the last one is fine
    (pkg / "stray.py").write_text("")
    problems = violations(tmp_path, unmoved_ok=False)
    assert any("engine/sneaky.py:1 imports foundry.data.store._atomic_write: a private name" in p for p in problems), problems
    assert any("engine/sneaky.py:2 imports jinja2" in p for p in problems), problems
    assert not any("engine/sneaky.py:3" in p for p in problems), problems
    assert any("data/bad.py:2 imports foundry.engine.runner" in p for p in problems), problems
    assert any("data/bad.py:3 imports fastapi" in p for p in problems), problems
    assert any("module foundry.stray is in no layer" in p for p in problems), problems


# --- services and api don't touch files or processes themselves ---------------------------------------------------

# Calls that read or write files or start processes. services and api go through foundry.data (files) and
# foundry.services.jobs (processes) instead; each exception below is the one module whose job that is.
FILE_CALLS = {"open", "read_text", "read_bytes", "write_text", "write_bytes", "iterdir", "glob", "rglob", "mkdir",
              "unlink", "exists", "is_file", "is_dir", "is_symlink", "rmdir", "rename", "touch", "stat"}
FILE_MODULES = {"shutil", "subprocess", "tempfile"}  # importing these at all; `os.<anything>(...)` calls and
# `from os import <anything but environ>` too (os.environ is fine, under any alias)
FILE_ALLOWED = {
    "foundry/services/jobs.py",  # starts `foundry` subprocesses with their stderr in a log file
    "foundry/api/uploads.py",  # streams an upload to disk under its size cap
}


def file_calls(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    os_names = {"os"} | {a.asname for n in ast.walk(tree) if isinstance(n, ast.Import)
                         for a in n.names if a.name == "os" and a.asname}
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            out += [(node.lineno, f"from os import {a.name}") for a in node.names if a.name != "environ"]
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "open":
                out.append((node.lineno, "open()"))
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in os_names:
                out.append((node.lineno, f"{f.value.id}.{f.attr}()"))
            elif isinstance(f, ast.Attribute) and f.attr in FILE_CALLS:
                out.append((node.lineno, f".{f.attr}()"))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            out += [(node.lineno, f"import {n}") for n in names if n.split(".")[0] in FILE_MODULES]
    return out


def test_services_and_api_dont_touch_files():
    found = []
    for layer in ("services", "api"):
        for path in sorted((SRC / "foundry" / layer).rglob("*.py")):
            rel = str(path.relative_to(SRC))
            if rel in FILE_ALLOWED:
                continue
            found += [f"{rel}:{line}: {what}" for line, what in file_calls(path)]
    assert not found, "use foundry.data (files) or foundry.services.jobs (processes):\n" + "\n".join(found)
    assert all((SRC / f).is_file() for f in FILE_ALLOWED), "an allowlisted module is gone; drop it from FILE_ALLOWED"


def test_the_file_check_catches_a_read(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("import shutil\nimport os\nfrom pathlib import Path\nPath('x').read_text()\nopen('y')\n"
                 "os.replace('a', 'b')\n'a'.replace('a', 'b')\nos.environ.get('X')\nimport os as o\no.remove('x')\n"
                 "from os import replace, environ\nimport tempfile\n")
    assert sorted(w for _, w in file_calls(f)) == [".read_text()", "from os import replace", "import shutil",
                                                   "import tempfile", "o.remove()", "open()", "os.replace()"]


def test_services_have_no_import_cycles():
    """foundry.services modules import each other in one direction only (the order in docs/architecture.md)."""
    graph = {}
    for path in (SRC / "foundry" / "services").glob("*.py"):
        me = module_name(path, SRC)
        graph[me] = {t for _, t, _ in imports(path, SRC) if t.startswith("foundry.services.") and t != me}
    state: dict[str, int] = {}

    def visit(m, stack):
        if state.get(m) == 1:
            raise AssertionError("import cycle: " + " -> ".join([*stack[stack.index(m):], m]))
        if state.get(m) == 2:
            return
        state[m] = 1
        for t in sorted(graph.get(m, ())):
            visit(t, [*stack, m])
        state[m] = 2

    for m in sorted(graph):
        visit(m, [])


# --- increment 16: the server-rendered UI is gone, and stays gone -------------------------------------------------

LEFTOVER = re.compile(r"\bclassic\b|\bjinja2?\b|\bmarkupsafe\b|monitor\.js|Jinja2Templates", re.IGNORECASE)
# Every remaining mention, each for a reason. A pair that no longer matches anything fails too, so this can't go stale.
LEFTOVER_ALLOWED = {
    ("README.md", "the server-rendered UI of increments 1–8 (`/classic`) was removed in increment 16"),
    ("tests/test_api.py", 'for path in ("/classic", "/classic/", "/classic/runs/x"):'),  # this allowlist's own entry
    ("tests/test_architecture.py", "LEFTOVER = re.compile("),  # this test's own patterns
    ("tests/test_architecture.py", 'if top in ("jinja2", "markupsafe"):'),
    ("tests/test_architecture.py", 'from foundry.data.store import _atomic_write\\nimport jinja2'),  # this allowlist's own entry (the checker's self-test)
    ("tests/test_architecture.py", 'assert any("engine/sneaky.py:2 imports jinja2"'),  # this allowlist's own entry
    ("tests/test_architecture.py", '("README.md", "the server-rendered UI'),  # the allowlist's own entries
    ("tests/test_architecture.py", "# this allowlist's own entry"),
}
SCANNED = ["src", "tests", "scripts", "web/src", "README.md"]


def _text_files():
    for base in SCANNED:
        p = ROOT / base
        files = [p] if p.is_file() else p.rglob("*")
        for f in files:
            if f.is_file() and not ({"__pycache__", "web_dist", "node_modules"} & set(f.parts)) \
                    and f.suffix in {".py", ".ts", ".tsx", ".mjs", ".md", ".css", ".html", ".json", ".toml", ".txt"}:
                yield f


def test_no_classic_ui_left():
    hits, used = [], set()
    for f in _text_files():
        rel = str(f.relative_to(ROOT))
        for n, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
            if not LEFTOVER.search(line):
                continue
            ok = [(p, s) for p, s in LEFTOVER_ALLOWED if p == rel and s in line]
            used.update(ok)
            if not ok:
                hits.append(f"{rel}:{n}: {line.strip()[:120]}")
    assert not hits, "leftovers of the removed server-rendered UI:\n" + "\n".join(hits)
    assert not LEFTOVER_ALLOWED - used, f"allowlisted but no longer present: {LEFTOVER_ALLOWED - used}"


def test_no_stale_module_paths():
    """Strings the import checks can't see: `-m` targets and package-data loads must name the moved modules."""
    stale = re.compile(r"-m\"?,? ?\"?foundry\.(harness|mcp_servers)\b|files\(\"foundry\.templates\"\)")
    hits = [f"{f.relative_to(ROOT)}:{n}" for base in ("src", "tests", "scripts", "config")
            for f in (ROOT / base).rglob("*") if f.is_file() and f.suffix in {".py", ".toml", ".mjs"}
            and "__pycache__" not in f.parts
            for n, line in enumerate(f.read_text(errors="replace").splitlines(), 1) if stale.search(line)]
    assert not hits, hits


def test_no_duplicate_test_names():
    """Two top-level `def test_x` in one module: the second silently replaces the first (ruff F811 isn't in CI)."""
    dups = []
    for f in (ROOT / "tests").glob("test_*.py"):
        names = [n.name for n in ast.parse(f.read_text()).body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
        dups += [f"{f.name}: {n}" for n in set(names) if names.count(n) > 1]
    assert not dups, dups


# --- one code path for the laptop and AWS (backend-parity design ADR-1) -------------------------------------------

# The backend modules: the cloud's state, job backend and worker. Only they touch the store, the mirror and the run
# index, or read `.cloud`; the rest of services and api go through `state.of(ws)` and `jobs.*`, so a feature written
# once works on both setups. Entry points (server, lambda_*, cloud_boot) and scripts are wiring and aren't checked.
BACKEND = {"foundry/services/cloud_state.py", "foundry/services/cloud_jobs.py", "foundry/services/workers.py"}
BACKEND_ONLY = ("foundry.data.store", "foundry.data.mirror", "foundry.data.index", "foundry.services.cloud_state",
                "foundry.services.cloud_jobs", "foundry.services.workers")


def backend_leaks(src: Path) -> list[str]:
    found = []
    for path in sorted(src.joinpath("foundry").rglob("*.py")):
        rel = path.relative_to(src).as_posix()
        if not rel.startswith(("foundry/services/", "foundry/api/")) or rel in BACKEND or "__pycache__" in path.parts:
            continue
        for line, target, name in imports(path, src):
            full = f"{target}.{name}" if name else target
            hit = next((m for m in BACKEND_ONLY if full == m or full.startswith(m + ".")), None)
            if hit:
                found.append(f"{rel}:{line} imports {hit}: only the backend modules may (use state.of(ws) / jobs.*)")
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Attribute) and node.attr == "cloud":
                found.append(f"{rel}:{node.lineno} reads .cloud: only the backend modules may (use state.of(ws))")
            if isinstance(node, ast.Call):  # the same, spelled dynamically
                name = ast.unparse(node.func)
                args = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                if name in ("getattr", "hasattr", "setattr") and "cloud" in args:
                    found.append(f"{rel}:{node.lineno} reads .cloud through {name}: only the backend modules may")
                if name in ("importlib.import_module", "import_module", "__import__") and any(
                        a == m or a.startswith(m + ".") for a in args for m in BACKEND_ONLY):
                    found.append(f"{rel}:{node.lineno} imports a backend module through {name}: only the backend "
                                 "modules may")
    return found


def test_only_backend_modules_know_the_cloud():
    problems = backend_leaks(SRC)
    assert not problems, "\n".join(problems)


def test_the_backend_check_catches_a_leak(tmp_path):
    """Each of the three checks reports: a module import, a function-local `from` import of a name, and `.cloud`."""
    pkg = tmp_path / "foundry"
    for d in ("", "services", "data", "api"):
        (pkg / d).mkdir(parents=True, exist_ok=True)
        (pkg / d / "__init__.py").write_text("")
    for m in ("data/store.py", "data/index.py", "services/cloud_state.py", "services/state.py"):
        (pkg / m).write_text("X = 1\n")
    (pkg / "services" / "leaky.py").write_text("from foundry.data import store\n"
                                               "def f(ws):\n    from foundry.services.cloud_state import X\n"
                                               "    return ws.cloud\n")
    (pkg / "api" / "fine.py").write_text("from foundry.services import state\n")
    (pkg / "api" / "sneaky.py").write_text("import importlib\ndef f(ws):\n    getattr(ws, 'cloud')\n"
                                           "    importlib.import_module('foundry.data.mirror')\n")
    (pkg / "services" / "cloud_state.py").write_text("from foundry.data import index\nY = lambda ws: ws.cloud\n")
    problems = backend_leaks(tmp_path)
    assert any("services/leaky.py:1 imports foundry.data.store" in p for p in problems), problems
    assert any("services/leaky.py:3 imports foundry.services.cloud_state" in p for p in problems), problems
    assert any("services/leaky.py:4 reads .cloud" in p for p in problems), problems
    assert any("api/sneaky.py:3 reads .cloud through getattr" in p for p in problems), problems
    assert any("api/sneaky.py:4 imports a backend module through importlib.import_module" in p for p in problems), problems
    assert not any("api/fine.py" in p or "cloud_state.py" in p for p in problems), problems
