# Live caption runs (deployment `embeddings`, build a0f8702-888cce56, 2026-11-03)

Agent: us.anthropic.claude-sonnet-4-6 · config bedrock-captions · evaluated in the deployed worker (Titan V2 embedder). The first two runs on build a0f8702 failed: rasterio couldn't load libexpat.so.1 in the Lambda image (fixed in Dockerfile.lambda, then redeployed).

## 2026-11-03T12-18-27Z_cap-01_3421

- step 1 llm_call /model ok 877.3 ms
- step 2 tool_call naip/naip_tile ok 17049.9 ms images=1
- step 3 llm_call /model ok 2222.9 ms

Caption:

> Large grain silos and an adjacent metal warehouse building dominate this arid, bare-dirt industrial yard. Scattered equipment, trailers, and small structures are spread across the compacted ground. A row of dense trees lines the right edge, and a paved road runs along the upper-right corner, contrasting sharply with the surrounding desert landscape.

overall 1.0 · workflow 1.0 · output 1.0 · scorer retrieval-v1 · judge None · embedder amazon.titan-embed-text-v2:0 · evaluation cost $0.000448
target t-r00-c03 ranked 1 of 8; references captions-stand-in-v0; sample m_3211513_se_11_060_20200524-r0-c27-20261103T112533Z
- t-r00-c03: caption 0.685, Clay 1.000
- t-r00-c02: caption 0.581, Clay 0.949
- t-r01-c03: caption 0.515, Clay 0.959
- t-r02-c03: caption 0.511, Clay 0.932
- t-r00-c01: caption 0.411, Clay 0.944
- t-r00-c00: caption 0.390, Clay 0.926
- t-r01-c04: caption 0.332, Clay 0.944
- t-r01-c02: caption 0.298, Clay 0.926

## 2026-11-03T12-18-57Z_cap-06_6d4a

- step 1 llm_call /model ok 937.3 ms
- step 2 tool_call naip/naip_tile ok 372.8 ms images=1
- step 3 llm_call /model ok 2032.7 ms

Caption:

> Sparse desert scrub and sandy bare ground dominate the tile, crossed diagonally by a two-lane road and a narrow irrigation canal or drainage channel lined with dense riparian vegetation. The road and vegetated waterway intersect near the center, creating a distinctive X-pattern against the pale sandy terrain.

overall 0.625 · workflow 1.0 · output 0.25 · scorer retrieval-v1 · judge None · embedder amazon.titan-embed-text-v2:0 · evaluation cost $0.000438
target t-r04-c04 ranked 4 of 8; references captions-stand-in-v0; sample m_3211513_se_11_060_20200524-r0-c27-20261103T112533Z
- t-r05-c03: caption 0.455, Clay 0.950
- t-r04-c05: caption 0.426, Clay 0.965
- t-r03-c02: caption 0.403, Clay 0.917
- t-r04-c04: caption 0.393, Clay 1.000
- t-r04-c06: caption 0.334, Clay 0.975
- t-r04-c07: caption 0.300, Clay 0.918
- t-r05-c01: caption 0.272, Clay 0.928
- t-r05-c07: caption 0.261, Clay 0.924

## Worker memory (CloudWatch REPORT lines, 12:10–12:25 UTC)

Caption runs and evaluations peaked at 389 MB; the ADB run (ac6-naip) at 699 MB, of 2048 MB.
