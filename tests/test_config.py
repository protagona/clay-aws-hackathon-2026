"""Config loading: the deployment overlay from the environment (aws-deploy design ADR-11)."""

from conftest import ROOT


def test_environment_overlay_for_the_cloud(monkeypatch):  # aws-deploy ADR-11: the image's RODA, no S3 mirror
    from foundry.config import load_config

    monkeypatch.setenv("FOUNDRY_S3_URI", "")
    monkeypatch.setenv("FOUNDRY_MCP_RODA_CMD", "/opt/uv-tools/bin/awslabs.roda-mcp-server")
    cfg = load_config(ROOT / "config" / "bedrock.toml")
    assert cfg.storage.s3_uri == ""
    roda = cfg.mcp.servers["roda"]
    assert roda.command == "/opt/uv-tools/bin/awslabs.roda-mcp-server" and roda.args == []
    assert roda.package == "awslabs.roda-mcp-server" and roda.version == "0.1.7"  # the recorded identity is kept
    assert cfg.mcp.servers["stac"].command == "{python}"  # only uvx servers are replaced


def test_no_overlay_without_the_environment(monkeypatch):
    from foundry.config import load_config

    monkeypatch.delenv("FOUNDRY_S3_URI", raising=False)
    monkeypatch.delenv("FOUNDRY_MCP_RODA_CMD", raising=False)
    cfg = load_config(ROOT / "config" / "bedrock.toml")
    assert cfg.mcp.servers["roda"].command == "uvx" and cfg.storage.s3_uri.startswith("s3://")
