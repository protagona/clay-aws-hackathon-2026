"""The cloud state store: one S3 bucket (objects) and one DynamoDB table (items), behind a small interface that a
test fake implements with the same semantics (aws-deploy design ADR-4, ADR-5).

Items are flat dicts of str, int, float or bool values; anything nested (manifests, cards, summaries) is stored
as a JSON string by the caller. Conditions use a small vocabulary (`Cond`) compiled to DynamoDB condition
expressions here and evaluated in Python by the fake, so tests exercise the real rules. Every client is pinned to
the deployment's region (spike S-5: the sandbox profile defaults to us-east-1) and S3 to SigV4 on the regional
virtual-hosted endpoint (presigned POSTs, ADR-10).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal

REGION = os.environ.get("AWS_REGION") or "us-west-2"


class NotFound(Exception):
    pass


class Conflict(Exception):
    """A condition failed (a version moved, a slot is taken, a cap would be crossed). `reasons` holds one code per
    transaction action: "ConditionalCheckFailed", "None", or (once retries ran out) "TransactionConflict"."""

    def __init__(self, reasons: list[str] | None = None):
        super().__init__(", ".join(reasons or ["ConditionalCheckFailed"]))
        self.reasons = reasons or ["ConditionalCheckFailed"]


# --- conditions -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cond:
    op: str  # absent | missing | eq | ne | lt | le | gt | in | all | any
    attr: str | None = None
    value: object = None
    parts: tuple = ()

    @staticmethod
    def absent() -> Cond:
        """The item doesn't exist."""
        return Cond("absent")

    @staticmethod
    def missing(attr: str) -> Cond:
        return Cond("missing", attr)

    @staticmethod
    def eq(attr: str, value) -> Cond:
        return Cond("eq", attr, value)

    @staticmethod
    def ne(attr: str, value) -> Cond:
        return Cond("ne", attr, value)

    @staticmethod
    def lt(attr: str, value) -> Cond:
        return Cond("lt", attr, value)

    @staticmethod
    def le(attr: str, value) -> Cond:
        return Cond("le", attr, value)

    @staticmethod
    def gt(attr: str, value) -> Cond:
        return Cond("gt", attr, value)

    @staticmethod
    def isin(attr: str, values) -> Cond:
        return Cond("in", attr, tuple(values))

    @staticmethod
    def all(*parts: Cond) -> Cond:
        return Cond("all", parts=parts)

    @staticmethod
    def any(*parts: Cond) -> Cond:
        return Cond("any", parts=parts)


def evaluate(cond: Cond | None, item: dict | None) -> bool:
    """The Python meaning of a condition (the fake store; and the reference for `compile_cond`)."""
    if cond is None:
        return True
    if cond.op == "absent":
        return item is None
    if cond.op == "all":
        return all(evaluate(p, item) for p in cond.parts)
    if cond.op == "any":
        return any(evaluate(p, item) for p in cond.parts)
    value = (item or {}).get(cond.attr)
    if cond.op == "missing":
        return value is None
    if value is None:  # DynamoDB: a comparison on a missing attribute is false
        return False
    if cond.op == "in":
        return value in cond.value
    if cond.op == "eq":
        return value == cond.value
    if cond.op == "ne":
        return value != cond.value
    if type(value) is not type(cond.value) and not all(isinstance(x, (int, float)) for x in (value, cond.value)):
        return False  # DynamoDB compares only like types
    return {"lt": value < cond.value, "le": value <= cond.value, "gt": value > cond.value}[cond.op]


class _Names:
    """Placeholders for one expression (attribute names and values)."""

    def __init__(self):
        self.names: dict[str, str] = {}
        self.values: dict[str, dict] = {}

    def name(self, attr: str) -> str:
        key = f"#n{len(self.names)}"
        self.names[key] = attr
        return key

    def value(self, v) -> str:
        key = f":v{len(self.values)}"
        self.values[key] = to_ddb(v)
        return key


def compile_cond(cond: Cond, n: _Names) -> str:
    if cond.op == "absent":
        return "attribute_not_exists(pk)"
    if cond.op in ("all", "any"):
        joiner = " AND " if cond.op == "all" else " OR "
        return "(" + joiner.join(compile_cond(p, n) for p in cond.parts) + ")"
    if cond.op == "missing":
        return f"attribute_not_exists({n.name(cond.attr)})"
    if cond.op == "in":
        return f"{n.name(cond.attr)} IN ({', '.join(n.value(v) for v in cond.value)})"
    sym = {"eq": "=", "ne": "<>", "lt": "<", "le": "<=", "gt": ">"}[cond.op]
    return f"{n.name(cond.attr)} {sym} {n.value(cond.value)}"


def to_ddb(v) -> dict:
    if isinstance(v, bool):
        return {"BOOL": v}
    if isinstance(v, (int, float)):
        return {"N": str(Decimal(str(v)))}
    if v is None:
        return {"NULL": True}
    return {"S": str(v)}


def from_ddb(v: dict):
    if "S" in v:
        return v["S"]
    if "N" in v:
        n = v["N"]
        return float(n) if any(c in n for c in ".eE") else int(n)
    if "BOOL" in v:
        return v["BOOL"]
    return None


# --- write actions ----------------------------------------------------------------------------------------------


@dataclass
class Put:
    item: dict  # includes pk and sk
    cond: Cond | None = None


@dataclass
class Update:
    pk: str
    sk: str
    set: dict = field(default_factory=dict)
    add: dict = field(default_factory=dict)  # numeric increments
    remove: tuple = ()
    cond: Cond | None = None


@dataclass
class Delete:
    pk: str
    sk: str
    cond: Cond | None = None


@dataclass
class Check:
    pk: str
    sk: str
    cond: Cond


def apply_update(item: dict | None, u: Update) -> dict:
    out = dict(item or {"pk": u.pk, "sk": u.sk})
    out.update(u.set)
    for k, v in u.add.items():
        out[k] = (out.get(k) or 0) + v
    for k in u.remove:
        out.pop(k, None)
    return out


# --- the AWS implementation -------------------------------------------------------------------------------------


class AwsStore:
    """S3 + DynamoDB through boto3 (low-level clients, explicit region, SigV4 regional S3)."""

    def __init__(self, bucket: str, table: str, region: str = REGION):
        import boto3
        from botocore.config import Config

        self.bucket, self.table, self.region = bucket, table, region
        self.s3 = boto3.client("s3", region_name=region,
                               config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"},
                                             max_pool_connections=32))  # parallel pulls (cloud_state.pull_all)
        self.ddb = boto3.client("dynamodb", region_name=region)

    @property
    def upload_origin(self) -> str:
        """Where presigned POSTs go: the bucket's regional virtual-hosted endpoint (the page's CSP allows it)."""
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com"

    # objects
    def put_object(self, key: str, data: bytes, if_none_match: bool = True) -> str:
        kw = {"IfNoneMatch": "*"} if if_none_match else {}
        try:
            r = self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, **kw)
        except self.s3.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise Conflict() from None
            raise
        return r.get("VersionId") or ""

    def get_object(self, key: str, version_id: str | None = None) -> bytes:
        kw = {"VersionId": version_id} if version_id else {}
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=key, **kw)["Body"].read()
        except self.s3.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "NoSuchVersion", "404"):
                raise NotFound(key) from None
            raise

    def delete_object(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)

    def presign_post(self, key: str, max_bytes: int, expires: int = 600) -> dict:
        return self.s3.generate_presigned_post(self.bucket, key, Conditions=[["content-length-range", 1, max_bytes]],
                                               ExpiresIn=expires)

    def object_size(self, key: str) -> int | None:
        try:
            return self.s3.head_object(Bucket=self.bucket, Key=key)["ContentLength"]
        except self.s3.exceptions.ClientError:
            return None

    # items
    def get_item(self, pk: str, sk: str) -> dict | None:
        r = self.ddb.get_item(TableName=self.table, Key={"pk": {"S": pk}, "sk": {"S": sk}}, ConsistentRead=True)
        return {k: from_ddb(v) for k, v in r["Item"].items()} if "Item" in r else None

    def query(self, pk: str, prefix: str = "", attrs: tuple = ()) -> list[dict]:
        kw = {"TableName": self.table, "KeyConditionExpression": "pk = :p" + (" AND begins_with(sk, :s)" if prefix else ""),
              "ExpressionAttributeValues": {":p": {"S": pk}, **({":s": {"S": prefix}} if prefix else {})},
              "ConsistentRead": True}
        if attrs:
            n = _Names()
            kw["ProjectionExpression"] = ", ".join(n.name(a) for a in ("pk", "sk", *attrs))
            kw["ExpressionAttributeNames"] = n.names
        out, start = [], None
        while True:
            r = self.ddb.query(**({**kw, "ExclusiveStartKey": start} if start else kw))
            out += [{k: from_ddb(v) for k, v in it.items()} for it in r["Items"]]
            start = r.get("LastEvaluatedKey")
            if not start:
                return out

    def incr(self, pk: str, sk: str, attr: str, by: int = 1, ttl: int | None = None) -> int:
        """Atomically add to a counter and return its new value (rate limits)."""
        n = _Names()
        expr = f"ADD {n.name(attr)} {n.value(by)}" + (f" SET {n.name('ttl')} = {n.value(ttl)}" if ttl else "")
        r = self.ddb.update_item(TableName=self.table, Key={"pk": {"S": pk}, "sk": {"S": sk}}, UpdateExpression=expr,
                                 ExpressionAttributeNames=n.names, ExpressionAttributeValues=n.values,
                                 ReturnValues="UPDATED_NEW")
        return int(from_ddb(r["Attributes"][attr]))

    def _action(self, a) -> dict:
        n = _Names()
        if isinstance(a, Put):
            body = {"TableName": self.table, "Item": {k: to_ddb(v) for k, v in a.item.items()}}
        elif isinstance(a, Delete):
            body = {"TableName": self.table, "Key": {"pk": {"S": a.pk}, "sk": {"S": a.sk}}}
        elif isinstance(a, Check):
            body = {"TableName": self.table, "Key": {"pk": {"S": a.pk}, "sk": {"S": a.sk}}}
        else:
            clauses = []
            if a.set:
                clauses.append("SET " + ", ".join(f"{n.name(k)} = {n.value(v)}" for k, v in a.set.items()))
            if a.add:
                clauses.append("ADD " + ", ".join(f"{n.name(k)} {n.value(v)}" for k, v in a.add.items()))
            if a.remove:
                clauses.append("REMOVE " + ", ".join(n.name(k) for k in a.remove))
            body = {"TableName": self.table, "Key": {"pk": {"S": a.pk}, "sk": {"S": a.sk}},
                    "UpdateExpression": " ".join(clauses)}
        if a.cond is not None:
            body["ConditionExpression"] = compile_cond(a.cond, n)
        if n.names:
            body["ExpressionAttributeNames"] = n.names
        if n.values:
            body["ExpressionAttributeValues"] = n.values
        return body

    def write(self, action) -> None:
        """One conditional write; Conflict when its condition fails."""
        body = self._action(action)
        call = {Put: self.ddb.put_item, Delete: self.ddb.delete_item, Update: self.ddb.update_item}[type(action)]
        try:
            call(**body)
        except self.ddb.exceptions.ConditionalCheckFailedException:
            raise Conflict() from None

    TRANSACT_TRIES = 5

    def transact(self, actions: list, token: str | None = None) -> None:
        """All-or-nothing; `token` (≤ 36 chars) makes a retried call idempotent for 10 minutes (spike S-5).

        A transaction cancelled only because another one was writing the same item (`TransactionConflict`: two
        starts on the month's SPEND record, say) changed nothing, so it is tried again, up to TRANSACT_TRIES times
        with jittered backoff and a fresh token per try (design ADR-8). A failed condition is never retried."""
        import random
        import time

        kinds = {Put: "Put", Update: "Update", Delete: "Delete", Check: "ConditionCheck"}
        items = [{kinds[type(a)]: self._action(a)} for a in actions]
        for attempt in range(self.TRANSACT_TRIES):
            kw = {"ClientRequestToken": (token[:33] + (f"-{attempt}" if attempt else ""))[:36]} if token else {}
            try:
                self.ddb.transact_write_items(TransactItems=items, **kw)
                return
            except self.ddb.exceptions.TransactionCanceledException as exc:
                reasons = [r.get("Code", "None") for r in exc.response.get("CancellationReasons", [])]
            if "TransactionConflict" not in reasons or "ConditionalCheckFailed" in reasons:
                break
            if attempt + 1 < self.TRANSACT_TRIES:
                time.sleep(random.uniform(0.05, 0.1) * 2 ** attempt)
        raise Conflict(reasons)


class BatchWorkflow:
    """Starts and reads the batch state machine (aws-deploy design ADR-7). One execution per batch job: the name is
    the job id, so a retried start can't run a batch twice."""

    def __init__(self, state_machine_arn: str, region: str = REGION):
        import boto3

        self.arn = state_machine_arn
        self.client = boto3.client("stepfunctions", region_name=region)

    def start(self, job_id: str) -> str:
        import json

        try:
            r = self.client.start_execution(stateMachineArn=self.arn, name=f"batch-{job_id}",
                                            input=json.dumps({"batch_job_id": job_id}))
        except self.client.exceptions.ExecutionAlreadyExists:
            return self.arn.replace(":stateMachine:", ":execution:") + f":batch-{job_id}"
        return r["executionArn"]

    def describe(self, execution_arn: str) -> str:
        return self.client.describe_execution(executionArn=execution_arn)["status"]


class LambdaInvoker:
    """Hands a job to the worker function: an async invoke (retries and event age are set on the function)."""

    def __init__(self, function_name: str, region: str = REGION):
        import boto3

        self.function_name = function_name
        self.client = boto3.client("lambda", region_name=region)

    def __call__(self, job_id: str) -> None:
        import json

        self.client.invoke(FunctionName=self.function_name, InvocationType="Event",
                           Payload=json.dumps({"job_id": job_id}).encode())
