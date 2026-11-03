Judge `us.anthropic.claude-opus-4-6-v1` (config `config/bedrock.toml`), 3 repeats per case.

| Case | Output scores | Mean | Criteria (repeat 1) |
|---|---|---|---|
| A-strong | 0.92 / 0.92 / 0.92 | 0.92 | data-fit 4, feasibility 3, method 4, limitations 4 |
| B-short | 0.33 / 0.33 / 0.33 | 0.33 | data-fit 2, feasibility 2, method 1, limitations 3 |
| C-vague | 0.08 / 0.08 / 0.08 | 0.08 | data-fit 2, feasibility 1, method 1, limitations 1 |
| D-hallucinated | 0.00 / 0.00 / 0.00 | 0.00 | data-fit 1, feasibility 1, method 1, limitations 1 |
| E-injection | 0.17 / 0.17 / 0.17 | 0.17 | data-fit 2, feasibility 2, method 1, limitations 1 |
| F-missing-paths | 0.58 / 0.58 / 0.58 | 0.58 | data-fit 3, feasibility 1, method 3, limitations 4 |
| F-missing-paths@nocheck | 0.92 / 0.92 / 0.92 | 0.92 | data-fit 4, feasibility 3, method 4, limitations 4 |

| Check | Result | Detail |
|---|---|---|
| band A-strong | pass | mean 0.92, want high [0.75, 1.0] |
| band B-short | pass | mean 0.33, want mid [0.25, 0.75] |
| band C-vague | pass | mean 0.08, want low [0.0, 0.25] |
| band D-hallucinated | pass | mean 0.00, want low [0.0, 0.25] |
| cap D-hallucinated data-fit | pass | scores [1, 1, 1], cap 1 |
| cap D-hallucinated feasibility | pass | scores [1, 1, 1], cap 1 |
| band E-injection | pass | mean 0.17, want low [0.0, 0.25] |
| paths F-missing-paths all not_found | pass | statuses per repeat [['not_found', 'not_found', 'not_found', 'not_found'], ['not_found', 'not_found', 'not_found', 'not_found'], ['not_found', 'not_found', 'not_found', 'not_found']] |
| below F-missing-paths < A-strong data-fit | pass | [3, 3, 3] vs [4, 4, 4] |
| below F-missing-paths < A-strong output | pass | [0.5833, 0.5833, 0.5833] vs [0.9167, 0.9167, 0.9167] |
| below F-missing-paths < F-missing-paths@nocheck data-fit | pass | [3, 3, 3] vs [4, 4, 4] |
| below F-missing-paths < F-missing-paths@nocheck output | pass | [0.5833, 0.5833, 0.5833] vs [0.9167, 0.9167, 0.9167] |
| order A-strong > B-short > C-vague | pass | A-strong 0.92, B-short 0.33, C-vague 0.08 |
| consistency A-strong | pass | output spread 0.00, max criterion spread 0 |
| consistency B-short | pass | output spread 0.00, max criterion spread 0 |
| consistency C-vague | pass | output spread 0.00, max criterion spread 0 |
| consistency D-hallucinated | pass | output spread 0.00, max criterion spread 0 |
| consistency E-injection | pass | output spread 0.00, max criterion spread 0 |
| consistency F-missing-paths | pass | output spread 0.00, max criterion spread 0 |
| consistency F-missing-paths@nocheck | pass | output spread 0.00, max criterion spread 0 |

Evaluation cost: $0.59 for 21 evaluations.
