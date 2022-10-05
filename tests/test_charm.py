# Copyright 2022 Gregory Schiano
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import unittest
from unittest.mock import MagicMock, Mock, patch

from ops import testing
from ops.model import ActiveStatus, Container, WaitingStatus
from ops.pebble import ServiceInfo
from ops.testing import Harness

from charm import PiholeOperatorCharm

testing.SIMULATE_CAN_CONNECT = True


class MockExecProcess(object):
    wait_output: MagicMock = MagicMock(return_value=("", None))


class MockExecAction(object):
    wait_output: MagicMock = MagicMock(return_value=("gravity domains: 15 ()", None))


class MockExecActionFailed(object):
    wait_output: MagicMock = MagicMock(return_value=("test", None))


class TestCharm(unittest.TestCase):
    @patch("charm.KubernetesServicePatch", lambda x, y, service_type: None)
    def setUp(self):
        self.harness = Harness(PiholeOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_config_changed(self):
        self.harness.set_leader(True)

        with patch.object(Container, "exec", return_value=MockExecProcess()), patch.object(
            Container,
            "push",
        ):
            self.harness.container_pebble_ready("pihole")
            self.harness.update_config({"web_port": "8080", "web_password": "test"})

        updated_plan = self.harness.get_container_pebble_plan("pihole").to_dict()
        updated_plan_service_env = updated_plan["services"]["pihole"]["environment"]
        # Can't get checks from the Ops harness
        # updated_plan_check_env = updated_plan["checks"]["lighttpd-check"]["http"]["url"]

        self.assertEqual("8080", updated_plan_service_env["WEB_PORT"])
        self.assertEqual("test", updated_plan_service_env["WEBPASSWORD"])
        # Can't get checks from the Ops harness
        # self.assertEqual("http://localhost:8080", updated_plan_check_env)

    def test_config_dns1_changed(self):
        self.harness.set_leader(True)

        with patch.object(Container, "exec", return_value=MockExecProcess()), patch.object(
            Container,
            "push",
        ):
            self.harness.container_pebble_ready("pihole")
            self.harness.update_config({"dns1": "1.1.1.1"})

        updated_plan = self.harness.get_container_pebble_plan("pihole").to_dict()
        updated_plan_service_env = updated_plan["services"]["pihole"]["environment"]

        self.assertEqual("1.1.1.1", updated_plan_service_env["PIHOLE_DNS_"])

    def test_config_dns2_changed(self):
        self.harness.set_leader(True)

        with patch.object(Container, "exec", return_value=MockExecProcess()), patch.object(
            Container,
            "push",
        ):
            self.harness.container_pebble_ready("pihole")
            self.harness.update_config({"dns2": "1.1.1.1"})

        updated_plan = self.harness.get_container_pebble_plan("pihole").to_dict()
        updated_plan_service_env = updated_plan["services"]["pihole"]["environment"]

        self.assertEqual("1.1.1.1", updated_plan_service_env["PIHOLE_DNS_"])

    def test_config_dns1_and_dns2_changed(self):
        self.harness.set_leader(True)

        with patch.object(Container, "exec", return_value=MockExecProcess()), patch.object(
            Container,
            "push",
        ):
            self.harness.container_pebble_ready("pihole")
            self.harness.update_config({"dns1": "1.1.1.1", "dns2": "2.2.2.2"})

        updated_plan = self.harness.get_container_pebble_plan("pihole").to_dict()
        updated_plan_service_env = updated_plan["services"]["pihole"]["environment"]

        self.assertEqual("1.1.1.1;2.2.2.2", updated_plan_service_env["PIHOLE_DNS_"])

    def test_config_changed_cant_connect(self):
        with patch.object(Container, "exec", return_value=MockExecProcess()), patch.object(
            Container,
            "push",
        ):
            self.harness.container_pebble_ready("pihole")
            self.harness.set_can_connect("pihole", False)
            self.harness.update_config({"web_port": "8080", "web_password": "test"})

        self.assertEqual(self.harness.model.unit.status, WaitingStatus("Waiting for pebble"))

    def test_config_changed_services_not_running(self):
        with patch.object(Container, "exec", return_value=MockExecProcess()), patch.object(
            ServiceInfo, "is_running", return_value=False
        ), patch.object(Container, "push"):
            self.harness.container_pebble_ready("pihole")
            self.harness.update_config({"web_port": "8080", "web_password": "test"})

        self.assertEqual(
            self.harness.model.unit.status, WaitingStatus("Waiting for pebble services")
        )

    def test_action(self):
        self.harness.set_can_connect("pihole", True)
        # the harness doesn't (yet!) help much with actions themselves
        action_event: Mock = Mock(params={"fail": ""})

        with patch.object(Container, "exec", return_value=MockExecAction()):
            self.harness.charm._on_update_gravity(action_event)

        expected_results = {"success": True, "number-of-gravity-domains": 15}
        action_event.set_results.assert_called_with(expected_results)

    def test_action_fail(self):
        # the harness doesn't (yet!) help much with actions themselves
        action_event: Mock = Mock(params={"fail": ""})

        with patch.object(Container, "exec", return_value=MockExecAction()):
            self.harness.charm._on_update_gravity(action_event)

        expected_results = {"success": False, "number-of-gravity-domains": 0}
        action_event.set_results.assert_called_with(expected_results)

    def test_action_fail_wrong_stdout(self):
        # the harness doesn't (yet!) help much with actions themselves
        action_event: Mock = Mock(params={"fail": ""})

        with patch.object(Container, "exec", return_value=MockExecActionFailed()):
            self.harness.charm._on_update_gravity(action_event)

        expected_results = {"success": False, "number-of-gravity-domains": 0}
        action_event.set_results.assert_called_with(expected_results)

    def test_pihole_pebble_ready(self):
        self.harness.set_can_connect("pihole", True)
        # Check the initial Pebble plan is empty
        initial_plan = self.harness.get_container_pebble_plan("pihole")
        self.assertEqual(initial_plan.to_yaml(), "{}\n")

        container = self.harness.model.unit.get_container("pihole")
        with patch.object(
            Container, "exec", return_value=MockExecProcess()
        ) as exec_mock, patch.object(
            Container,
            "push",
        ) as patch_push:
            self.harness.charm.on.pihole_pebble_ready.emit(container)

            env_config = {
                "WEB_PORT": "80",
                "WEBPASSWORD": "admin",
                "PIHOLE_DNS_": "",
                # Since pebble exec command doesn't copy the container env,
                # I need to take the required envVars for the application to work properly
                "PH_VERBOSE": "0",
                "PIHOLE_INSTALL": "/etc/.pihole/automated install/basic-install.sh",
                "PHP_ENV_CONFIG": "/etc/lighttpd/conf-enabled/15-fastcgi-php.conf",
                "PHP_ERROR_LOG": "/var/log/lighttpd/error-pihole.log",
                "FTLCONF_LOCAL_IPV4": "charm-dev",
                "FTL_CMD": "no-daemon",
                "DNSMASQ_USER": "pihole",
            }

            exec_mock.assert_any_call(["/usr/local/bin/_startup.sh"], environment=env_config)

            patch_push.assert_called_with(
                "/usr/local/bin/_pihole-FTL.sh",
                self.harness.charm._get_script("pihole-FTL.sh"),
                permissions=0o770,
            )

            expected_plan = {
                "services": {
                    "cron": {
                        "override": "replace",
                        "summary": "Cron service",
                        "command": "/usr/sbin/cron -f -l 2",
                        "startup": "enabled",
                    },
                    "pihole": {
                        "override": "replace",
                        "summary": "pihole-FTL service",
                        "command": "/usr/local/bin/_pihole-FTL.sh",
                        "startup": "enabled",
                        "environment": env_config,
                    },
                    "lighttpd": {
                        "override": "replace",
                        "summary": "Lighttpd service",
                        "command": "/usr/sbin/lighttpd -D -f /etc/lighttpd/lighttpd.conf",
                        "startup": "enabled",
                        "environment": env_config,
                        "after": ["pihole"],
                    },
                }
            }

            updated_plan = self.harness.get_container_pebble_plan("pihole").to_dict()
            self.assertDictEqual(expected_plan, updated_plan)

            # Check the service was started
            for service_name in ["pihole", "lighttpd", "cron"]:
                service = container.get_service(service_name)
                self.assertTrue(service.is_running())

            exec_mock.assert_any_call(
                ["/usr/local/bin/_gravityonboot.sh"],
                environment=updated_plan["services"]["pihole"]["environment"],
            )
            self.assertEqual(self.harness.charm._stored.gravityonboot, True)

            # Ensure we set an ActiveStatus with no message
            self.assertEqual(self.harness.model.unit.status, ActiveStatus())

    def test_pihole_pebble_ready_service_not_started(self):
        self.harness.set_can_connect("pihole", True)
        container = self.harness.model.unit.get_container("pihole")
        with patch.object(
            Container, "exec", return_value=MockExecProcess()
        ) as exec_mock, patch.object(ServiceInfo, "is_running", return_value=False), patch.object(
            Container, "push"
        ):
            self.harness.charm.on.pihole_pebble_ready.emit(container)

            self.assertEqual(self.harness.charm._stored.gravityonboot, False)
            self.assertEqual(
                self.harness.model.unit.status, WaitingStatus("Waiting for pebble services")
            )
            # Gravity on boot not called
            exec_mock.assert_called_once()
