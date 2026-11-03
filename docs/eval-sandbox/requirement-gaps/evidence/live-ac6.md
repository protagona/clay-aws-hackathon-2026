# AC6 live: requester-pays NAIP path check in the deployed worker (2026-11-03)

Crafted prompt `ac6-naip` (added through POST /api/prompts) asking the plan to cite the 2020 CA 60 cm NAIP prefix. Run 2026-11-03T12-21-22Z_ac6-naip_1a21, Kimi K3, config bedrock, evaluated by the worker with config/bedrock.toml.

```json
{
 "path_checker": "v2",
 "paths": {
  "version": "v2",
  "checked": 4,
  "found": 4,
  "not_found": 0,
  "unverified": 0,
  "items": [
   {
    "location": "s3://naip-analytic/ca/2020/60cm/rgbir_cog/32115/",
    "status": "found",
    "reason": "found (requester pays)"
   },
   {
    "location": "s3://naip-analytic/ca/2020/60cm/rgbir_cog/32115/m_3211512_sw_11_060_20200524.tif",
    "status": "found",
    "reason": "found (requester pays)"
   },
   {
    "location": "s3://naip-analytic/ca/2020/60cm/fgdc/32115/",
    "status": "found",
    "reason": "found (requester pays)"
   },
   {
    "location": "s3://naip-analytic/",
    "status": "found",
    "reason": "found (requester pays)"
   }
  ]
 },
 "overall": 1.0
}
```

An imported OTLP trace citing NAIP (otel-ac6-crafted-…) was evaluated with config/offline.toml (imports record no config, so the deployment defaults to offline, where the path check is off): existing behaviour, noted as a follow-up.
