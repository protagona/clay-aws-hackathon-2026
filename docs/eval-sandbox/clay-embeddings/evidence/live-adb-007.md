# Live run 2026-11-03T04-17-44Z_adb-007_198f (build ad5506c, deployment `embeddings`, 2026-11-03)

status succeeded · model us.moonshotai.kimi-k3 · overall 1.0 · workflow 1.0 · output 1.0

## Steps (from the cloud trace)

-  1 model  model                                       2567.8 ms 
-  2 tool   embeddings/embedding_coverage                626.7 ms 
-  3 tool   roda/search_datasets                        2047.2 ms 
-  4 model  model                                       2732.8 ms 
-  5 tool   roda/get_dataset_details                       6.8 ms 
-  6 tool   embeddings/embedding_change                27433.8 ms 
-  7 model  model                                       4490.1 ms 
-  8 tool   stac/get_item                                405.0 ms 
-  9 tool   stac/search_items                            416.3 ms 
- 10 tool   stac/get_item                                420.8 ms 
- 11 model  model                                       9031.7 ms 

## Path check (scores.json `paths`)

```json
{
 "version": "v1",
 "checked": 2,
 "found": 2,
 "not_found": 0,
 "unverified": 0,
 "items": [
  {
   "location": "s3://sentinel-cogs/",
   "status": "found",
   "reason": ""
  },
  {
   "location": "s3://us-west-2.opendata.source.coop/clay/lgnd-embeddings/",
   "status": "found",
   "reason": ""
  }
 ]
}
```

path_checker: v1

## Sequence

```json
{
 "stages": [
  [
   "embedding_change"
  ],
  [
   "search_items",
   "get_item"
  ]
 ],
 "matched": 2,
 "missing": [],
 "expected_sha256": "d66f2ac0edf260e74334a853ca1cac8660e041b90b0333ea5832a33954b0a264"
}
```

## Judge

- **data-fit** 4.0/4.0: The plan selects Clay v1.5 embeddings for screening and Sentinel-2 L2A COGs for the 10 m change layer, both well-suited to the task. Resolution (1.28 km for screening, 10 m for detailed change), temporal coverage (July 2020 and July 2024), and spectral bands are justified. Datasets were confirmed via registry lookups and STAC queries, with specific scene IDs and cloud cover verified.
- **feasibility** 4.0/4.0: Access details are complete and correct: S3 bucket (sentinel-cogs, confirmed by evaluator), region (us-west-2), format (COG GeoTIFF), not Requester-Pays, free anonymous read, STAC endpoint, and license (Copernicus Sentinel). Embeddings bucket also confirmed. The note about not mixing collections and the COG streaming approach via /vsis3/ show practical awareness of cost and access controls.
- **method** 4.0/4.0: Processing steps are concrete, ordered, and complete: export AOIs from embedding change cells, STAC query for matched scene pairs, stream COG bands with window reads, cloud masking with SCL, UTM harmonization, multi-index change detection (CVA, ΔNDVI, ΔNDBI, ΔNDMI, ΔBSI), thresholding, vectorization, and delivery format. Validation is included (compare against TCI thumbnails before scaling).
- **limitations** 4.0/4.0: Limitations are specific and tied to the chosen data and method: coarse Clay cell resolution, date mixing within the monthly mosaic, irrigation/fallow cycle confusion, SCL masking and cross-UTM reprojection artifacts, threshold sensitivity. Mitigations are offered: analyst review, TCI thumbnail validation, suggestion to use NAIP for finer confirmation.
