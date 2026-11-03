# Vision spike: an image returned by an MCP tool, through Strands, to Bedrock models (2026-11-03)

**Setup:**
- A stdio MCP server built on mcp 2.1.1 `MCPServer`. Its tool `get_tile` returns `[Image(png 64×64 solid red), json]`.
- Strands 1.62.0 `MCPClient` → `Agent(BedrockModel(model_id=…, region_name="us-west-2"))`.
- **Prompt:** "Call get_tile with tile_id 'x', then tell me in one word the dominant colour of the image."
- **Account:** sandbox 283171504459, profile `protagona-sandbox-1`.

| Model id | Result |
|---|---|
| `us.moonshotai.kimi-k3` | ValidationException: "This model doesn't support the image field for user messages. Remove image and try again." |
| `moonshotai.kimi-k2.5` | Same ValidationException |
| `qwen.qwen3-vl-235b-a22b` | Same ValidationException |
| `us.amazon.nova-2-lite-v1:0` | Answered that the tool returned no visual information (image ignored) |
| `us.anthropic.claude-sonnet-4-6` | "**Red.**" |
| `us.anthropic.claude-haiku-4-5-20251001-v1:0` | "Red." |

`aws bedrock list-foundation-models --region us-west-2` lists `inputModalities` `TEXT` and `IMAGE` for Kimi K3, Kimi K2.5 and Qwen3-VL. That listing doesn't mean an image is accepted inside a Converse tool result.
