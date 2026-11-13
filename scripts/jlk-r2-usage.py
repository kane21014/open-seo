"""Conservative early warning for account-wide R2 Standard usage."""
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

TOKEN = os.environ["CF_TOKEN"]
ACCOUNT = os.environ["CF_ACCOUNT"]
QUERY = """
query Usage($account: string!, $start: Time!, $end: Time!) {
  viewer { accounts(filter: {accountTag: $account}) {
    operations: r2OperationsAdaptiveGroups(limit: 1000, filter: {datetime_geq: $start, datetime_lt: $end}) {
      dimensions {actionType} sum {requests}
    }
    storage: r2StorageAdaptiveGroups(limit: 10000, filter: {datetime_geq: $start, datetime_lt: $end}) {
      dimensions {bucketName} max {payloadSize metadataSize}
    }
  } }
}
"""
free_ops = {"deleteobject", "deletebucket", "abortmultipartupload"}
class_b_ops = {"headbucket", "headobject", "getobject", "usagesummary", "getbucketencryption",
               "getbucketlocation", "getbucketcors", "getbucketlifecycleconfiguration"}
class_a = class_b = peak_bytes = 0
end = datetime.now(timezone.utc).replace(microsecond=0)
# Rolling 31 days covers the current billing cycle irrespective of its start.
# Daily windows avoid wide query limits. Unknown operations count as Class A.
for days in range(31):
    stop = end - timedelta(days=days)
    variables = {"account": ACCOUNT, "start": (stop - timedelta(days=1)).isoformat(),
                 "end": stop.isoformat()}
    request = urllib.request.Request("https://api.cloudflare.com/client/v4/graphql",
        data=json.dumps({"query": QUERY, "variables": variables}).encode(),
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=45) as response:
        data = json.load(response)
    if data.get("errors") or not data.get("data", {}).get("viewer", {}).get("accounts"):
        raise SystemExit("R2 MONITOR UNAVAILABLE: verify Account Analytics Read and dataset access.")
    account = data["data"]["viewer"]["accounts"][0]
    if len(account["storage"]) >= 10000 or len(account["operations"]) >= 1000:
        raise SystemExit("R2 MONITOR UNAVAILABLE: incomplete analytics response.")
    peak_bytes = max(peak_bytes, sum(row["max"]["payloadSize"] + row["max"]["metadataSize"]
                                     for row in account["storage"]))
    for row in account["operations"]:
        operation = row["dimensions"]["actionType"].lower()
        if operation in class_b_ops:
            class_b += row["sum"]["requests"]
        elif operation not in free_ops:
            class_a += row["sum"]["requests"]
for label, value, limit in [("storage", peak_bytes, 10_000_000_000),
                            ("Class A", class_a, 1_000_000), ("Class B", class_b, 10_000_000)]:
    if value >= limit * 0.8:
        raise SystemExit(f"R2 USAGE WARNING: {label} reached the 80% early-warning threshold.")
print("R2 usage check passed.")
