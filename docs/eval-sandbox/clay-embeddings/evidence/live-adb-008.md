# Live run 2026-11-03T04-18-59Z_adb-008_3bff (build ad5506c, deployment `embeddings`, 2026-11-03)

status succeeded · model us.moonshotai.kimi-k3 · overall 1.0 · workflow 1.0 · output 1.0

## Steps (from the cloud trace)

-  1 model  model                                       2001.0 ms 
-  2 tool   embeddings/embedding_coverage                791.9 ms 
-  3 tool   roda/search_datasets                        1609.8 ms 
-  4 model  model                                       2778.9 ms 
-  5 tool   embeddings/similar_places                   4912.9 ms 
-  6 model  model                                       2875.1 ms 
-  7 tool   roda/get_dataset_details                       7.6 ms 
-  8 tool   stac/get_item                                296.6 ms 
-  9 tool   stac/search_items                            315.5 ms 
- 10 model  model                                       5576.4 ms 

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
   "location": "s3://us-west-2.opendata.source.coop/clay/lgnd-embeddings/monthly-aggregated/model_version=v1.5/",
   "status": "found",
   "reason": ""
  },
  {
   "location": "s3://sentinel-cogs/",
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
   "similar_places"
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

- **data-fit** 4.0/4.0: The plan uses Clay v1.5 embeddings for similarity search and Sentinel-2 L2A for verification, both confirmed via registry/STAC tools. Resolution (1.28 km cells for discovery, 10 m for verification), coverage (Terai region within half a degree), timing (monsoon month 2024-08), and spectral bands (NIR, SWIR, SCL) are all justified against the flood-resilience survey question.
- **feasibility** 4.0/4.0: Both S3 paths were verified as existing. Bucket names, regions (us-west-2), formats (COG), licenses (CC BY 4.0, Copernicus terms), and cost (free, not Requester-Pays) are all explicitly stated. The plan includes a practical cost-control measure of window-reading only cell footprints rather than downloading full tiles.
- **method** 4.0/4.0: Processing steps are concrete, ordered, and complete: export candidate cells as GeoJSON, query for low-cloud scenes, window-read specific bands, compute flood indicators (NDWI/MNDWI, SCL water class, NDVI drop), and flag/rank cells for survey prioritization. Validation is built in through step 4-5 confirmation against spectral indices.
- **limitations** 4.0/4.0: Specific, data-tied limitations are stated: monsoon cloud cover (36% on the key scene), embeddings capturing landscape similarity rather than confirmed flooding, 1.28 km cell coarseness, and missing 2021 months. Mitigations are provided for each: SAR/later dates for clouds, verification step for false positives, 10 m mapping for field-level work, and restricting to 2024 months.
