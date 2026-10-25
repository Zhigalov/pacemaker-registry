"""Deployment must preserve the owner's existing production settings."""
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("deploy_ydb", Path(__file__).parents[1] / "ops/deploy_ydb.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


def revision():
    return {
        "container_id": "existing-container", "image": {"environment": {"YANDEX_METRICA_ID": "123"}},
        "resources": {"memory": "268435456", "cores": "1", "core_fraction": "100"},
        "provision_policy": {"min_instances": "1"}, "execution_timeout": "15s", "concurrency": "8",
        "service_account_id": "existing-account", "connectivity": {"network_id": "existing-network"},
        "secrets": [{"id": "reference-only", "version_id": "version", "key": "pg-password",
                     "environment_variable": "DATABASE_PASSWORD"}],
        "log_options": {"log_group_id": "existing-log-group"}, "metadata_options": {},
    }


def test_deployment_preserves_resources_network_and_secret_references():
    args = deploy.deploy_arguments(revision(), "image:tested-commit")
    for key, value in (("--container-id", "existing-container"), ("--memory", "268435456B"),
                       ("--cores", "1"), ("--core-fraction", "100"), ("--min-instances", "1"),
                       ("--network-id", "existing-network"), ("--service-account-id", "existing-account"),
                       ("--concurrency", "8"), ("--execution-timeout", "15s"),
                       ("--log-group-id", "existing-log-group")):
        assert args[args.index(key) + 1] == value
    assert "DATABASE_BACKEND=ydb" in args
    assert "YANDEX_METRICA_ID=123" in args
    assert "YDB_METADATA_CREDENTIALS=1" in args
    assert "id=reference-only,version-id=version,key=pg-password,environment-variable=DATABASE_PASSWORD" in args


def test_deployment_refuses_inline_secrets():
    source = revision()
    source["image"]["environment"]["DATABASE_PASSWORD"] = "not-a-real-password"
    with pytest.raises(ValueError, match="Inline credentials"):
        deploy.deploy_arguments(source, "image")


def test_deployment_refuses_accidental_cold_start_configuration():
    source = revision()
    source["provision_policy"]["min_instances"] = "0"
    with pytest.raises(ValueError, match="min_instances"):
        deploy.deploy_arguments(source, "image")


def test_deployment_refuses_to_silently_drop_nondefault_settings():
    source = revision()
    source["metadata_options"] = {"gce_http_endpoint": "DISABLED"}
    with pytest.raises(ValueError, match="metadata"):
        deploy.deploy_arguments(source, "image")
