"""The batch workflow (aws-deploy design ADR-7): Prepare → Map{item} → Finalize, on the worker function.

Each item task has no automatic retry (a retried item would re-run paid work) except on
Lambda.TooManyRequestsException, where the function never ran. Each catches every error into a Pass that keeps the
item's input (`result_path: $.error`), so one failed item never fails the inline Map; inline Map has no failure
tolerance setting (state-map-inline.html). Prepare and the Map itself catch into Finalize with the error, so a
batch's reservation is always settled. Spike S-3 proved the shape (MaxConcurrencyPath, per-item Catch).
"""

from __future__ import annotations

from aws_cdk import Duration
from aws_cdk import aws_lambda as lam
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct


def batch_state_machine(scope: Construct, worker: lam.IFunction, timeout: Duration = Duration.hours(12)) -> sfn.StateMachine:
    def invoke(cid: str, payload: dict, result_path: str | None = None) -> tasks.LambdaInvoke:
        t = tasks.LambdaInvoke(scope, cid, lambda_function=worker, payload=sfn.TaskInput.from_object(payload),
                               payload_response_only=True, retry_on_service_exceptions=False,
                               result_path=result_path)
        t.add_retry(errors=["Lambda.TooManyRequestsException"], max_attempts=3, backoff_rate=2,
                    interval=Duration.seconds(5))
        return t

    finalize = invoke("Finalize", {"kind": "batch_finalize", "batch_job_id.$": "$.batch_job_id",
                                   "error.$": "$.error"})
    finalize_ok = invoke("FinalizeOk", {"kind": "batch_finalize", "batch_job_id.$": "$.batch_job_id"})
    prepare = invoke("Prepare", {"kind": "batch_prepare", "batch_job_id.$": "$.batch_job_id"})
    item = invoke("Item", {"kind": "batch_item", "item.$": "$"})
    item.add_catch(sfn.Pass(scope, "ItemFailed"), errors=["States.ALL"], result_path="$.error")
    items = sfn.Map(scope, "Items", items_path="$.items", max_concurrency_path="$.concurrency", result_path="$.results")
    items.item_processor(item)
    with_error = sfn.Pass(scope, "KeepBatchId", parameters={"batch_job_id.$": "$.batch_job_id", "error.$": "$.error"})
    prepare.add_catch(with_error, errors=["States.ALL"], result_path="$.error")
    items.add_catch(with_error, errors=["States.ALL"], result_path="$.error")
    with_error.next(finalize)
    chain = prepare.next(items).next(finalize_ok)
    return sfn.StateMachine(scope, "BatchWorkflow", definition_body=sfn.DefinitionBody.from_chainable(chain),
                            timeout=timeout)
