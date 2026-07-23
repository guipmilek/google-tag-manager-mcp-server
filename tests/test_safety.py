from __future__ import annotations

import asyncio
import base64
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from gtm_mcp import auth
from gtm_mcp.safety import (
    CRUD_CONTRACT_VERSION,
    canonical_json,
    load_scope_config,
    operation_hash,
    validate_scope,
)
from gtm_mcp.tools import TOOL_DEFINITIONS, gtm_batch_operations


class SafetyTests(unittest.TestCase):
    def test_shared_credential_envelope_materializes_adc(self) -> None:
        credentials = {"type": "service_account", "project_id": "test"}
        encoded = base64.b64encode(
            json.dumps({"google_credentials": credentials}).encode()
        ).decode()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "adc.json"
            with (
                patch.dict(
                    os.environ, {"MCP_CREDENTIALS": encoded}, clear=True
                ),
                patch.object(auth, "_ADC_PATH", target),
            ):
                configured = auth.configure_deployment_credentials()
                self.assertEqual(target, configured)
                self.assertEqual(credentials, json.loads(target.read_text()))

    def test_canonical_json_and_hash_are_stable(self) -> None:
        left = {"z": 1, "a": {"d": 4, "b": 2}, "list": [{"y": 2, "x": 1}]}
        right = {"list": [{"x": 1, "y": 2}], "a": {"b": 2, "d": 4}, "z": 1}
        self.assertEqual(canonical_json(left), canonical_json(right))
        self.assertEqual(operation_hash(left), operation_hash(right))
        self.assertEqual(len(operation_hash(left)), 32)

    def test_obsolete_gate_environment_is_ignored(self) -> None:
        environment = {
            "MCP_CONFIG": json.dumps(
                {
                    "accounts": ["1"],
                    "containers": ["2"],
                    "workspaces": ["3"],
                }
            ),
            "GTM_MUTATIONS_ENABLED": "false",
            "GTM_ALLOW_DELETE": "false",
            "GTM_CONFIRMATION_SECRET": "unused",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = load_scope_config()
        self.assertEqual(config.allowed_account_ids, frozenset({"1"}))
        validate_scope(
            config,
            account_id="1",
            container_id="2",
            workspace_id="3",
        )

    def test_missing_config_allows_all_accessible_resources(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = load_scope_config()
        validate_scope(
            config,
            account_id="1",
            container_id="2",
            workspace_id="3",
        )
        self.assertEqual(config.max_operations, 10)

    def test_dry_run_never_calls_mutation_executor(self) -> None:
        normalized = [
            {
                "resource": "folder",
                "action": "create",
                "account_id": "1",
                "container_id": "2",
                "workspace_id": "3",
                "resource_id": None,
                "parent": "accounts/1/containers/2/workspaces/3",
                "path": None,
                "data": {"name": "codex-test"},
                "workspace_fingerprint": "wf",
                "resource_fingerprint": None,
                "no_op_reason": None,
            }
        ]
        with (
            patch("gtm_mcp.tools.load_scope_config", return_value=Mock()),
            patch("gtm_mcp.tools.get_gtm_service", return_value=Mock()),
            patch(
                "gtm_mcp.tools.normalize_operations",
                new=AsyncMock(return_value=normalized),
            ),
            patch("gtm_mcp.tools.execute_one", new=AsyncMock()) as execute,
        ):
            result = asyncio.run(
                gtm_batch_operations(
                    "1",
                    "2",
                    "3",
                    [
                        {
                            "resource": "folder",
                            "action": "create",
                            "data": {"name": "x"},
                        }
                    ],
                    dry_run=True,
                )
            )
        self.assertEqual(result["contract_version"], CRUD_CONTRACT_VERSION)
        self.assertEqual(result["execution_status"], "NOT_EXECUTED")
        self.assertFalse(result["verification"]["google_api_mutation_sent"])
        execute.assert_not_awaited()

    def test_idempotent_delete_noop_sends_no_mutation(self) -> None:
        normalized = [
            {
                "resource": "folder",
                "action": "delete",
                "account_id": "1",
                "container_id": "2",
                "workspace_id": "3",
                "resource_id": "4",
                "parent": "accounts/1/containers/2/workspaces/3",
                "path": "accounts/1/containers/2/workspaces/3/folders/4",
                "data": None,
                "workspace_fingerprint": "wf",
                "resource_fingerprint": None,
                "no_op_reason": "ALREADY_ABSENT",
            }
        ]
        with (
            patch("gtm_mcp.tools.load_scope_config", return_value=Mock()),
            patch("gtm_mcp.tools.get_gtm_service", return_value=Mock()),
            patch(
                "gtm_mcp.tools.normalize_operations",
                new=AsyncMock(return_value=normalized),
            ),
            patch("gtm_mcp.tools.execute_one", new=AsyncMock()) as execute,
        ):
            result = asyncio.run(
                gtm_batch_operations(
                    "1",
                    "2",
                    "3",
                    [
                        {
                            "resource": "folder",
                            "action": "delete",
                            "resource_id": "4",
                        }
                    ],
                )
            )
        self.assertEqual(result["execution_status"], "SUCCEEDED")
        self.assertEqual(result["results"][0]["outcome"], "ALREADY_ABSENT")
        self.assertFalse(result["execution_attempted"])
        execute.assert_not_awaited()

    def test_public_write_signatures_have_no_confirmation_gate(self) -> None:
        write_definitions = [
            definition for definition in TOOL_DEFINITIONS if not definition[2]
        ]
        self.assertTrue(write_definitions)
        for (
            function,
            _title,
            _read_only,
            _destructive,
            _idempotent,
        ) in write_definitions:
            parameters = inspect.signature(function).parameters
            self.assertNotIn("confirmation", parameters)
            self.assertNotIn("validate_only", parameters)
            self.assertIn("dry_run", parameters)

    def test_delete_annotation_is_truthful(self) -> None:
        by_name = {
            definition[0].__name__: definition
            for definition in TOOL_DEFINITIONS
        }
        delete = by_name["gtm_delete_resource"]
        self.assertFalse(delete[2])
        self.assertTrue(delete[3])
        self.assertTrue(delete[4])


if __name__ == "__main__":
    unittest.main()
