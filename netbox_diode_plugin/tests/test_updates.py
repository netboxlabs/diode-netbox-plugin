#!/usr/bin/env python
# Copyright 2024 NetBox Labs Inc
"""Diode NetBox Plugin - Tests."""

import inspect
import json
import logging
import os
from unittest import mock

from rest_framework import status
from utilities.testing import APITestCase

from netbox_diode_plugin.api.authentication import DiodeOAuth2Authentication
from netbox_diode_plugin.api.common import harmonize_formats
from netbox_diode_plugin.api.plugin_utils import get_object_type_model
from netbox_diode_plugin.plugin_config import get_diode_user

logger = logging.getLogger(__name__)


def load_test_cases(cls):
    """Class decorator to load test cases and create test methods."""
    logger.error("**** Loading test cases")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    test_data_path = os.path.join(current_dir, "test_updates_cases.json")
    logger.error(f"**** Looking for test data at {test_data_path}")

    if not os.path.exists(test_data_path):
        logger.error(f"**** Test data file not found at {test_data_path}")
        raise FileNotFoundError(f"Test data file not found at {test_data_path}")

    def _create_and_update_test_case(case):
        object_type = case["object_type"]

        def test_func(self):
            model = get_object_type_model(object_type)

            payload = {
                "timestamp": 1,
                "object_type": object_type,
                "entity": case["create"],
            }
            res = self.send_request(self.diff_url, payload)
            self.assertEqual(res.status_code, status.HTTP_200_OK)
            diff = res.json().get("change_set", {})
            res = self.client.post(
                self.apply_url, data=diff, format="json", **self.authorization_header
            )
            self.assertEqual(res.status_code, status.HTTP_200_OK)
            # lookup the object and check fields
            obj = model.objects.get(**case["lookup"])
            self._check_expect(obj, case["create_expect"])

            # resending the same payload should not change anything
            payload = {
                "timestamp": 2,
                "object_type": object_type,
                "entity": case["create"],
            }
            res = self.send_request(self.diff_url, payload)
            self.assertEqual(res.status_code, status.HTTP_200_OK)

            change_set = res.json().get("change_set", {})
            if change_set.get("changes", []) != []:
                logger.error(f"Unexpected change set {json.dumps(change_set, indent=4)}")

            self.assertEqual(res.json().get("change_set", {}).get("changes", []), [])

            # updating the object
            payload = {
                "timestamp": 3,
                "object_type": object_type,
                "entity": case["update"],
            }
            res = self.send_request(self.diff_url, payload)
            self.assertEqual(res.status_code, status.HTTP_200_OK)

            diff = res.json().get("change_set", {})
            res = self.client.post(
                self.apply_url, data=diff, format="json", **self.authorization_header
            )
            self.assertEqual(res.status_code, status.HTTP_200_OK)
            obj = model.objects.get(**case["lookup"])
            self._check_expect(obj, case["update_expect"])

        test_func.__name__ = f"test_updates_{case['name']}"
        return test_func

    with open(test_data_path) as f:
        test_cases = json.load(f)
        for case in test_cases:
            t = _create_and_update_test_case(case)
            logger.error(f"**** Creating test case {t.__name__}")
            setattr(cls, t.__name__, t)

    return cls

@load_test_cases
class ApplyUpdatesTestCase(APITestCase):
    """diff/create/update test cases."""

    @classmethod
    def setUpClass(cls):
        """Set up the test cases."""
        super().setUpClass()

    def setUp(self):
        """Set up the test case."""
        self.diff_url = "/netbox/api/plugins/diode/generate-diff/"
        self.apply_url = "/netbox/api/plugins/diode/apply-change-set/"
        self.authorization_header = {"Authorization": "Bearer mocked_oauth_token"}
        self.diode_user = get_diode_user()
        self.auth_patcher = mock.patch.object(
            DiodeOAuth2Authentication,
            'authenticate',
            return_value=(self.diode_user, None)
        )
        self.auth_patcher.start()

    def tearDown(self):
        """Clean up after tests."""
        self.auth_patcher.stop()
        super().tearDown()

    def _follow_path(self, obj, path):
        cur = obj
        for i, p in enumerate(path):
            if p.isdigit():
                p = int(p)
                cur = cur[p]
            else:
                cur = getattr(cur, p)
            if i != len(path) - 1:
                self.assertIsNotNone(cur)
            if callable(cur):
                try:
                    signature = inspect.signature(cur)
                    if len(signature.parameters) == 0:
                        cur = cur()
                except ValueError:
                    pass
        return harmonize_formats(cur)

    def _check_set_by(self, obj, path, value):
        key = path[-1][len("__by_"):]
        path = path[:-1]
        cur = self._follow_path(obj, path)

        if isinstance(value, (list, tuple)):
            vals = set(value)
        else:
            vals = {value}

        cvals = {harmonize_formats(getattr(c, key)) for c in cur}
        self.assertEqual(cvals, vals)

    def _check_equals(self, obj, path, value):
        cur = self._follow_path(obj, path)
        self.assertEqual(cur, value)

    def _check_expect(self, obj, expect):
        for field, value in expect.items():
            path = field.strip().split(".")
            if path[-1].startswith("__by_"):
                self._check_set_by(obj, path, value)
            else:
                self._check_equals(obj, path, value)

    def send_request(self, url, payload, status_code=status.HTTP_200_OK):
        """Post the payload to the url and return the response."""
        response = self.client.post(
            url, data=payload, format="json", **self.authorization_header
        )
        self.assertEqual(response.status_code, status_code)
        return response
