"""Deployment names (named-deployments spec N1, N2): several deployments can share one AWS account, each in its own
pair of stacks. No name is the main deployment, whose stacks keep their original names."""

from __future__ import annotations

import re

MAIN = "main"
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,23}$")


def check(name: str | None) -> str:
    """The deployment name to use: MAIN for none (or "main"), else a valid name; ValueError otherwise."""
    if not name or name == MAIN:
        return MAIN
    if not NAME_RE.fullmatch(name) or name.endswith("-") or "--" in name:
        raise ValueError(f"deployment name {name!r}: 1-24 lowercase letters, digits and single '-', starting with a "
                         "letter")
    return name


def stack_names(name: str | None) -> tuple[str, str]:
    """(data stack, app stack) for a deployment."""
    name = check(name)
    return ("FoundryDataStack", "FoundryAppStack") if name == MAIN else (f"Foundry-{name}-Data", f"Foundry-{name}-App")
