"""Write foundry-remote.pyz, the remote CLI as one file (aws-deploy spec I3), for handing out without a deployment.

    uv run python scripts/build_cli.py [out.pyz]      # default: dist/foundry-remote.pyz

A deployment serves the same bytes at GET /cli/foundry-remote.pyz (built from the installed source on demand).
"""

import sys
from pathlib import Path

from foundry.ui.spa import cli_pyz

out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "dist" / "foundry-remote.pyz")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(cli_pyz())
out.chmod(0o755)
print(f"wrote {out} ({out.stat().st_size} bytes)")
