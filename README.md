# Clay Earth Dataset Foundry: Agentic Eval Sandbox

Breaking Barriers US 2026 hackathon, Team 46, for Renaissance Philanthropy (Clay).

**Goal:** Build an observability and evaluation sandbox. It should prove that an agentic geospatial workflow can be **observed, summarized, and scored** end to end.

## Start here

1. [Requirements](docs/requirements.md) covers the Must, Should, Could and Should-not lists in plain language.
2. [Visuals](docs/visuals.md) holds the loop, the architecture, the timeline, the checkpoint board, and a chart for deciding whether to work on something.
3. [Trace contract](docs/trace-contract.md) is the shared file format that lets everyone work in parallel. Ratify it during kickoff.
4. [Backlog](docs/backlog.md) splits the work into streams, with dependencies and "done when" criteria for each item.

Source material from the organizers:

- [Vision](docs/Clay-Foundry-Vision-Hackathon.md)
- [Technical](docs/Clay-Foundry-Technical-Hackathon.md)

## Local setup

Tool versions are pinned in `.tool-versions` (uv, Node, AWS CLI) and `.python-version` (Python, installed by uv).

1. Install [asdf](https://asdf-vm.com) 0.16+ and put its shims on your PATH: `export PATH="${ASDF_DATA_DIR:-$HOME/.asdf}/shims:$PATH"`.
2. From the repo root:

   ```bash
   asdf plugin add uv && asdf plugin add nodejs && asdf plugin add awscli
   asdf install
   uv python install
   ```

Python comes from uv rather than asdf. That way nothing needs compiling: asdf would build CPython from source, and 3.12 doesn't build against Homebrew's OpenSSL 4.

Not covered by asdf: a container runtime for CDK asset bundling (`brew install colima docker docker-buildx`), and the CDK CLI (pinned in the project once K-5 is decided).

## License

Apache-2.0. The LICENSE file is added at kickoff (K-2).
