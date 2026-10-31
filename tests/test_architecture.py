"""The layers of src/foundry and the rules between them (increment 16; docs/architecture.md).

    kernel    config, contract                  settings and run-record schemas; any layer may import them
    data      foundry.data.*                    files only: safe reads and writes
    engine    foundry.engine.*                  agents, models, MCP servers, tracing, scoring, batch, import
    services  foundry.services.*                what the app does
    api       foundry.api.*                     HTTP only
    ui        foundry.ui.*                      serves the built web app
    entry     foundry.cli, __main__, server     wire the layers together; may import anything

Dependencies point down only. The checks below parse every module with `ast` (imports inside functions count too).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

KERNEL = {"foundry", "foundry.config", "foundry.contract"}
ENTRY = {"foundry.cli", "foundry.__main__", "foundry.server"}
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
UNMOVED = {
    "foundry.rubrics": "services",  # rubrics/cli.py and rubrics/service.py (slice 5)
    **{f"foundry.ui.{m}": "services" for m in ("actions", "app", "api", "dashboard", "monitor", "render")},
}


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


def imports(path: Path, src: Path) -> list[tuple[int, str]]:
    """Every foundry module (and web framework) this file imports, with the line. `from foundry import x` resolves to
    foundry.x when x is a module; relative imports are resolved against the file's package."""
    tree = ast.parse(path.read_text(), filename=str(path))
    me = module_name(path, src)
    package = me if path.name == "__init__.py" else me.rpartition(".")[0]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".")
                anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join(anchor + ([base] if base else []))
            for a in node.names:
                full = f"{base}.{a.name}"
                is_module = (src / Path(*full.split("."))).with_suffix(".py").exists() or (src / Path(*full.split("."))).is_dir()
                out.append((node.lineno, full if is_module else base))
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
        for line, target in imports(path, src):
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
            elif theirs != mine and re.search(r"\._[a-z]", "." + target.rpartition(".")[2]):
                found.append(f"{where} imports {target}: a private name of another layer")
    return found


def test_layers():
    problems = violations(SRC)
    assert not problems, "\n".join(problems)


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
    (pkg / "stray.py").write_text("")
    problems = violations(tmp_path, unmoved_ok=False)
    assert any("data/bad.py:2 imports foundry.engine.runner" in p for p in problems), problems
    assert any("data/bad.py:3 imports fastapi" in p for p in problems), problems
    assert any("module foundry.stray is in no layer" in p for p in problems), problems


# --- increment 16: the server-rendered UI is gone, and stays gone -------------------------------------------------

LEFTOVER = re.compile(r"\bclassic\b|\bjinja2?\b|\bmarkupsafe\b|monitor\.js|Jinja2Templates", re.IGNORECASE)
# Every remaining mention, each for a reason. A pair that no longer matches anything fails too, so this can't go stale.
LEFTOVER_ALLOWED = {
    ("README.md", "the server-rendered UI of increments 1–8 (`/classic`) was removed in increment 16"),
    ("tests/test_api.py", 'for path in ("/classic", "/classic/", "/classic/runs/x"):'),  # this allowlist's own entry
    ("tests/test_architecture.py", "LEFTOVER = re.compile("),  # this test's own patterns
    ("tests/test_architecture.py", 'if top in ("jinja2", "markupsafe"):'),
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
