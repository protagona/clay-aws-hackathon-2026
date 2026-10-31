"""`foundry rubric draft|list|activate|discard` (increment 8)."""

from __future__ import annotations

import sys
from pathlib import Path

from foundry.config import ConfigError, load_config
from foundry.engine.models.base import ProviderError
from foundry.rubrics.service import acquire, create_draft
from foundry.engine.rubrics.sources import SourceError
from foundry.data.rubrics import Store, StoreError
from foundry.data.tasks import RubricError, task_dir


def add_parser(sub) -> None:
    rb = sub.add_parser("rubric", help="import, review and activate rubrics (any format) with history")
    rsub = rb.add_subparsers(dest="rubric_command", required=True)
    d = rsub.add_parser("draft", help="draft a rubric from a file, text, URL or MCP tool with the config's judge")
    d.add_argument("--task", default="adb")
    d.add_argument("--config", default="config/bedrock.toml", help="its [judge] drafts the rubric (Bedrock)")
    src = d.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="pdf, docx, xlsx, csv, md, txt, html or json")
    src.add_argument("--text", help="a few sentences, or pasted output of another tool")
    src.add_argument("--url", help="an https URL")
    src.add_argument("--mcp", help="SERVER/TOOL from the config's [mcp.servers]")
    d.add_argument("--args", default="", help="JSON arguments for --mcp")
    ls = rsub.add_parser("list", help="the active rubric, its history and pending drafts")
    ls.add_argument("--task", default="adb")
    ls.add_argument("--runs-dir", default="runs")
    a = rsub.add_parser("activate", help="activate a reviewed draft, or a version from the history")
    a.add_argument("--task", default="adb")
    a.add_argument("target", help="a draft id, or a history file name such as adb-rubric-v0@0.1.json")
    x = rsub.add_parser("discard", help="delete a draft")
    x.add_argument("--task", default="adb")
    x.add_argument("draft_id")


def main(args) -> int:
    store_dir = task_dir(args.task)
    if not (store_dir / "prompts.jsonl").exists():
        print(f"Unknown task '{args.task}'", file=sys.stderr)
        return 2
    store = Store(store_dir)
    try:
        if args.rubric_command == "draft":
            cfg = load_config(args.config)
            data = Path(args.file).read_bytes() if args.file else b""
            kind = "file" if args.file else "text" if args.text else "url" if args.url else "mcp"
            source = acquire(kind, cfg, file_name=args.file or "", file_data=data, text=args.text or "",
                             url=args.url or "", mcp=args.mcp or "", args=args.args)
            draft_id, meta = create_draft(store_dir, source, cfg)
            print(f"draft {draft_id}: {'valid' if meta['valid'] else 'INVALID: ' + str(meta['error'])}")
            for note in meta.get("notes") or []:
                print(f"  note: {note}")
            print(f"review: {store_dir / 'rubrics' / 'drafts' / draft_id / 'draft.json'} "
                  f"(or the UI's Rubric tab), then: foundry rubric activate --task {args.task} {draft_id}")
            return 0 if meta["valid"] else 1
        if args.rubric_command == "list":
            versions = store.versions(Path(args.runs_dir))
            if not any(v["active"] for v in versions):
                from foundry.data.tasks import load_rubric

                r, _ = load_rubric(store_dir / "rubric.json")
                print(f"* {r.rubric_id} v{r.version} (rubric.json; enters the history on the first activation)")
            for v in versions:
                print(f"{'*' if v['active'] else ' '} {v['file']:<40} {v.get('activated_at') or '(original)':<26} "
                      f"{v['runs']} runs")
            for m in store.drafts():
                print(f"  draft {m['draft_id']} {'valid' if m['valid'] else 'invalid'} from {m['source']['label']}")
            return 0
        if args.rubric_command == "activate":
            e = (store.activate_version(args.target) if args.target.endswith(".json")
                 else store.activate_draft(args.target))
            print(f"active: {e['rubric_id']} v{e['version']} ({e['file']})")
            return 0
        store.discard(args.draft_id)
        print(f"discarded {args.draft_id}")
        return 0
    except (SourceError, StoreError, ConfigError, ProviderError, OSError, RubricError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
