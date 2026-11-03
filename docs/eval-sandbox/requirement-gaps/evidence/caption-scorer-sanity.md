# Caption scorer sanity (bedrock amazon.titan-embed-text-v2:0, sample m_3211513_se_11_060_20200524-r0-c27-20261103T112533Z)

| Prompt | Target | Own | Generic | Other (tile) | Keyword-stuffed |
|---|---|---|---|---|---|
| cap-01 | t-r00-c03 | 1.00 | 0.20 | 0.33 (t-r03-c07) | 0.20 |
| cap-02 | t-r01-c01 | 1.00 | 0.14 | 0.20 (t-r02-c02) | 0.12 |
| cap-03 | t-r01-c04 | 1.00 | 0.17 | 0.14 (t-r05-c03) | 0.14 |
| cap-04 | t-r00-c04 | 1.00 | 0.50 | 0.50 (t-r04-c02) | 0.25 |
| cap-05 | t-r03-c03 | 1.00 | 0.14 | 1.00 (t-r05-c03) | 0.17 |
| cap-06 | t-r04-c04 | 1.00 | 0.17 | 0.17 (t-r02-c06) | 1.00 |
| cap-07 | t-r05-c02 | 1.00 | 0.33 | 0.17 (t-r01-c01) | 0.33 |
| cap-08 | t-r06-c05 | 1.00 | 0.17 | 0.17 (t-r03-c00) | 0.14 |
| cap-09 | t-r06-c03 | 1.00 | 0.33 | 0.17 (t-r00-c00) | 0.33 |
| cap-10 | t-r06-c01 | 1.00 | 0.14 | 0.25 (t-r02-c07) | 1.00 |
| cap-11 | t-r03-c06 | 1.00 | 0.14 | 0.14 (t-r03-c07) | 0.20 |
| cap-12 | t-r04-c02 | 1.00 | 0.25 | 0.14 (t-r02-c01) | 0.14 |

Keyword-stuffed mean: 0.34 (recorded, not checked)

- PASS: own reference scores 1.0 on all 12
- PASS: generic caption <= 0.5 on at least 10 of 12
- PASS: other tile's reference below own on at least 10 of 12
