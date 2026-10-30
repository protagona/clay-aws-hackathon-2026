import os
import sys

import aws_cdk as cdk

sys.path.insert(0, os.path.dirname(__file__))
from stack import FoundryStack  # noqa: E402

app = cdk.App()
FoundryStack(app, "ClayFoundryEvalSandbox",
             env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region="us-west-2"))
app.synth()
