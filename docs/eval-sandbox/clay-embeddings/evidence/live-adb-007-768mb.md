# Live run 2026-11-03T04-24-52Z_adb-007_9958 (build 922e710: DuckDB limit 768MB, deployment `embeddings`, 2026-11-03)

status succeeded · model us.moonshotai.kimi-k3

## Steps (from the cloud trace)

-  1 model  model                                       2078.3 ms 
-  2 tool   embeddings/embedding_coverage                523.9 ms 
-  3 model  model                                       1852.3 ms 
-  4 tool   embeddings/embedding_change                15626.4 ms 
-  5 model  model                                       9124.8 ms 
-  6 tool   stac/get_item                                301.2 ms 
-  7 tool   stac/get_item                                329.4 ms 
-  8 tool   roda/search_datasets                         915.1 ms 
-  9 model  model                                      11910.4 ms 
- 10 tool   roda/get_dataset_details                      13.5 ms ERROR
- 11 tool   stac/search_items                            106.9 ms 
- 12 model  model                                       3422.0 ms 
- 13 tool   roda/get_dataset_details                       4.4 ms 
- 14 model  model                                      34557.6 ms 

Step 10's error is the agent's: RODA has no dataset slug `sentinel-2-l2a`; it recovered at step 13.

## Lambda worker REPORT for this run

Duration: 91360.11 ms · Max Memory Used: 1688 MB (of 2048 MB). With DuckDB at 1GB the earlier adb-007 run peaked at 1741 MB.
