"""The CDK app for the serverless deployment (aws-deploy): FoundryDataStack and FoundryAppStack only. Run through
scripts/cloud.sh, which passes the context below; the legacy FoundryStack (app.py) is never synthesized here.

    -c email=<alerts address>  (required)   -c budget=<usd>  (optional)   -c cap=<usd>  (default 30; 0 = no cap)
    -c build=<build id>        (default dev)
"""

import os
import sys

import aws_cdk as cdk

sys.path.insert(0, os.path.dirname(__file__))
from foundry_cloud.stacks import FoundryAppStack, FoundryDataStack  # noqa: E402

app = cdk.App()
ctx = app.node.try_get_context
email = ctx("email")
if not email:
    raise SystemExit("cloud_app.py: -c email=<address> is required (alerts go there)")
cap = float(ctx("cap") or 30)
env = cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region="us-west-2")
data = FoundryDataStack(app, "FoundryDataStack", email=email, budget_usd=float(ctx("budget") or 0) or None,
                        cap_usd=cap or None, api_concurrency=int(ctx("concurrency") or 20), env=env)
FoundryAppStack(app, "FoundryAppStack", data=data, build=ctx("build") or "dev", cap_usd=cap or None, env=env)
app.synth()
