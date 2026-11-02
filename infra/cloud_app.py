"""The CDK app for the serverless deployment (aws-deploy): FoundryDataStack and FoundryAppStack only. Run through
scripts/cloud.sh, which passes the context below; the legacy FoundryStack (app.py) is never synthesized here.

    -c email=<alerts address>  (required)   -c budget=<usd>  (optional)   -c cap=<usd>  (default 30; 0 = no cap)
    -c build=<build id>        (default dev)        -c deployment=<name>  (default: main; named-deployments N1)
"""

import os
import sys

import aws_cdk as cdk

sys.path.insert(0, os.path.dirname(__file__))
from foundry_cloud.names import check, stack_names  # noqa: E402
from foundry_cloud.stacks import FoundryAppStack, FoundryDataStack  # noqa: E402

app = cdk.App()
ctx = app.node.try_get_context
email = ctx("email")
if not email:
    raise SystemExit("cloud_app.py: -c email=<address> is required (alerts go there)")
cap = float(ctx("cap") or 30)
try:
    deployment = check(ctx("deployment"))
except ValueError as exc:
    raise SystemExit(f"cloud_app.py: {exc}") from None
data_name, app_name = stack_names(deployment)
env = cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region="us-west-2")
concurrency = ctx("concurrency")
data = FoundryDataStack(app, data_name, email=email, budget_usd=float(ctx("budget") or 0) or None, cap_usd=cap or None,
                        api_concurrency=int(concurrency) if concurrency not in (None, "") else 20, deployment=deployment,
                        env=env)
FoundryAppStack(app, app_name, data=data, build=ctx("build") or "dev", cap_usd=cap or None, deployment=deployment,
                env=env)
app.synth()
