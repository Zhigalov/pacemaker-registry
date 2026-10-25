#!/usr/bin/env python3
"""Deploy YDB using the existing Serverless revision's configuration.

Dry-run by default. Does not touch DNS, the VM, PG, IAM, or Lockbox contents.
The original environment and secret *references* are preserved; secret values
are neither fetched nor printed.
"""
import argparse
import json
import re
import subprocess


def yc(*arguments):
    result = subprocess.run(["yc", *arguments, "--format", "json"], capture_output=True, text=True)
    if result.returncode:
        # Useful diagnostics without printing full requests/environments.
        error = re.search(r"^ERROR: (.*)$", result.stderr, re.MULTILINE)
        request = re.search(r"client-request-id: ([a-zA-Z0-9-]+)", result.stderr)
        detail = error.group(1) if error else "Cloud command failed"
        if request:
            detail += "; request-id=" + request.group(1)
        raise RuntimeError(detail + "; inspect operation status before retrying")
    return json.loads(result.stdout)


def deploy_arguments(revision, image):
    environment = dict(revision["image"].get("environment", {}))
    environment.update(DATABASE_BACKEND="ydb",
        YDB_ENDPOINT="grpcs://ydb.serverless.yandexcloud.net:2135",
        YDB_DATABASE="/ru-central1/b1g2035eiff72jcbalkd/etn685h40uqd8416ire4",
        YDB_METADATA_CREDENTIALS="1")
    if any("PASSWORD" in key or "TOKEN" in key or "KEY_CREDENTIALS" in key for key in environment):
        raise ValueError("Inline credentials are not supported; use existing secret references")
    resources = revision["resources"]
    policy = revision["provision_policy"]
    if int(policy["min_instances"]) < 1:
        raise ValueError("Expected existing min_instances >= 1")
    args = ["serverless", "container", "revision", "deploy",
            "--container-id", revision["container_id"], "--image", image,
            "--memory", str(resources["memory"]) + "B", "--cores", str(resources["cores"]),
            "--core-fraction", str(resources["core_fraction"]),
            "--execution-timeout", revision["execution_timeout"],
            "--concurrency", str(revision["concurrency"]),
            "--service-account-id", revision["service_account_id"],
            "--network-id", revision["connectivity"]["network_id"],
            "--min-instances", str(policy["min_instances"]),
            "--description", "Verified PostgreSQL to YDB migration"]
    for key, value in environment.items():
        args += ["--environment", f"{key}={value}"]
    for secret in revision.get("secrets", []):
        args += ["--secret", ",".join(f"{key.replace('_', '-')}={value}" for key, value in secret.items())]
    for key in ("zone_instances_limit", "zone_requests_limit"):
        if key in policy:
            args += ["--" + key.replace('_', '-'), str(policy[key])]
    logs = revision.get("log_options", {})
    if logs.get("disabled"):
        args += ["--no-logging"]
    elif logs.get("log_group_id"):
        args += ["--log-group-id", logs["log_group_id"]]
    elif logs.get("folder_id"):
        args += ["--log-folder-id", logs["folder_id"]]
    if logs.get("min_level"):
        args += ["--min-log-level", logs["min_level"].lower()]
    if revision.get("metadata_options"):
        raise ValueError("Non-default metadata options need explicit preservation")
    if revision.get("mounts") or revision.get("image", {}).get("command") or revision.get("image", {}).get("args"):
        raise ValueError("Non-default mounts/entrypoint need explicit preservation")
    return args


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-revision", default="bba1hgjnjta5ivi16jic")
    parser.add_argument("--image", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    revision = yc("serverless", "container", "revision", "get", args.source_revision)
    arguments = deploy_arguments(revision, args.image)
    if not args.apply:
        print("Dry run: preserve resources, network, service account, secrets and min_instances; backend=ydb")
        return
    deployed = yc(*arguments)
    if (deployed["resources"] != revision["resources"]
            or deployed["connectivity"] != revision["connectivity"]
            or deployed["provision_policy"] != revision["provision_policy"]
            or deployed.get("secrets") != revision.get("secrets")
            or deployed["service_account_id"] != revision["service_account_id"]):
        raise RuntimeError("Deployment configuration postcondition failed; inspect before DNS cutover")
    print(json.dumps({"revision_id": deployed['id'], "status": deployed['status'],
                      "backend": "ydb", "resources": deployed['resources'],
                      "min_instances": deployed['provision_policy']['min_instances']}))


if __name__ == "__main__":
    main()
