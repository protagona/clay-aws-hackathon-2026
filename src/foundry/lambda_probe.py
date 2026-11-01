"""AWS Lambda entry point for the probe (aws-deploy spec O2): every 5 minutes, GET the public /healthz and record
Foundry/Probe Healthy = 1 or 0. An alarm emails when it's 0 (or missing) twice in 10 minutes. No token, no data."""

from __future__ import annotations

import os
import urllib.request


def healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=20) as r:
            return r.status == 200 and r.read().startswith(b"ok")
    except OSError:
        return False


def handler(event, context):  # noqa: ARG001 (Lambda's signature)
    import boto3

    ok = healthy(os.environ["PROBE_URL"])
    boto3.client("cloudwatch").put_metric_data(Namespace="Foundry/Probe", MetricData=[
        {"MetricName": "Healthy", "Value": 1.0 if ok else 0.0, "Unit": "Count"}])
    return {"healthy": ok}
