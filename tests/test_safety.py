from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from gtm_mcp.safety import (
    SafetyError,
    canonical_json,
    issue_confirmation,
    load_safety_config,
    operation_hash,
    verify_and_register_confirmation,
)


class SafetyTests(unittest.TestCase):
    def test_canonical_json_and_hash_are_stable(self) -> None:
        left = {"z": 1, "a": {"d": 4, "b": 2}, "list": [{"y": 2, "x": 1}]}
        right = {"list": [{"x": 1, "y": 2}], "a": {"b": 2, "d": 4}, "z": 1}
        self.assertEqual(canonical_json(left), canonical_json(right))
        self.assertEqual(operation_hash(left), operation_hash(right))
        self.assertEqual(len(operation_hash(left)), 32)

    def test_confirmation_is_bound_and_single_use_per_process(self) -> None:
        payload = {"stage": "TEST", "operations": [{"x": 1}]}
        scope = {
            "stage": "TEST",
            "accountIds": ["1"],
            "containerIds": ["2"],
            "workspaceIds": ["3"],
            "operationCount": 1,
        }
        with patch.dict(os.environ, {"GTM_CONFIRMATION_SECRET": "x" * 48}, clear=False):
            receipt = issue_confirmation(
                payload,
                verb="EXECUTE",
                stage="TEST",
                scope=scope,
                ttl_seconds=900,
            )
            verified = verify_and_register_confirmation(
                receipt["required_confirmation"],
                payload,
                expected_verb="EXECUTE",
                stage="TEST",
                scope=scope,
            )
            self.assertTrue(verified["confirmation_verified"])
            with self.assertRaisesRegex(SafetyError, "already registered"):
                verify_and_register_confirmation(
                    receipt["required_confirmation"],
                    payload,
                    expected_verb="EXECUTE",
                    stage="TEST",
                    scope=scope,
                )

    def test_environment_is_fail_closed(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = load_safety_config()
        self.assertFalse(config.mutations_enabled)
        self.assertFalse(config.allow_publish)
        self.assertEqual(config.max_operations, 10)
        self.assertEqual(config.allowed_account_ids, frozenset())


if __name__ == "__main__":
    unittest.main()
