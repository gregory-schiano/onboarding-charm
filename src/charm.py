#!/usr/bin/env python3
# Copyright 2022 Gregory Schiano
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

    https://discourse.charmhub.io/t/4208
"""

import logging
import re

from pathlib import Path
from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, MaintenanceStatus, WaitingStatus, BlockedStatus
from ops.pebble import ExecError, Layer
from charms.observability_libs.v1.kubernetes_service_patch import KubernetesServicePatch
from lightkube.models.core_v1 import ServicePort

logger = logging.getLogger(__name__)


class PiholeOperatorCharm(CharmBase):
    """Charm the service."""

    _stored: StoredState = StoredState()

    def __init__(self, *args):
        super().__init__(*args)
        self._name = "pihole"
        self._scripts_dir = "src/scripts/"

        self._stored.set_default(
            gravityonboot=False
        )

        http_port = ServicePort(
            int(self.config["web_port"]),
            name=f"{self._name}-web",
            targetPort=80
        )
        dns_tcp_port = ServicePort(
            53,
            name=f"{self._name}-tcp-dns",
            targetPort=53
        )
        dns_udp_port = ServicePort(
            53,
            name=f"{self._name}-udp-dns",
            protocol="UDP",
            targetPort=53
        )
        self.service_patcher = KubernetesServicePatch(self, [
            http_port,
            dns_tcp_port,
            dns_udp_port
        ], service_type="NodePort")

        self.framework.observe(self.on.pihole_pebble_ready, self._on_pihole_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.update_gravity_action, self._on_update_gravity)

    def _on_pihole_pebble_ready(self, event):
        """Define and start a workload using the Pebble API."""

        logger.info("_on_pihole_pebble_ready")

        # Get a reference the container attribute on the PebbleReadyEvent
        container = event.workload
        
        # Reset state
        self._stored.gravityonboot = False

        # First call the startup script, must be called before services are started
        logger.debug("Executing startup script")
        self._execute_script(container, "_startup.sh", "/usr/local/bin")
        
        # Push required script
        logger.debug("Pushing pihole-FTL script")
        container.push(
            "/usr/local/bin/_pihole-FTL.sh",
            self._get_script("pihole-FTL.sh"),
            permissions=0o770
        )

        logger.debug("Configuring pebble layer")
        # Define an initial Pebble layer configuration
        pihole_pebble_config = self._config_pebble_pihole(container)
        # Add initial Pebble config layer using the Pebble API
        container.add_layer(self._name, pihole_pebble_config, combine=True)
        # Autostart any services that were defined with startup: enabled
        container.autostart()

        # Either pebble_ready wasn't yet emitted and there no services, or
        # services aren't yet started
        service_list = container.get_services()
        if not service_list or self._not_running_services(service_list):
            self.unit.status = WaitingStatus("Waiting for pebble services")
            return

        logger.debug("Services are ready, run Gravityonboot")
        self._execute_script(container, "_gravityonboot.sh", "/usr/local/bin")
        self._stored.gravityonboot = True
        self.unit.status = ActiveStatus()

    def _on_config_changed(self, event):
        """ Config has changed """
        
        logger.info("_on_config_changed")

        container = self.unit.get_container(self._name)
        if not container.can_connect():
            logger.debug("Container is not ready")
            self.unit.status = WaitingStatus("Waiting for pebble")
            event.defer()
            return
        logger.debug("Container is ready")

        # Either pebble_ready wasn't yet emitted and there no services, or
        # services aren't yet started
        service_list = container.get_services()
        if not service_list or self._not_running_services(service_list):
            logger.debug("Services aren't yet planned or not working")
            self.unit.status = WaitingStatus("Waiting for pebble services")
            event.defer()
            return
        logger.debug("Services are ready")

        # Must be run only when all services are up & running
        if not self._stored.gravityonboot:
            logger.debug("Gravityonboot never ran, running it")
            self.model.unit.status = MaintenanceStatus("Executing gravityonboot script")
            self._execute_script(container, "_gravityonboot.sh", "/usr/local/bin")
            self._stored.gravityonboot = True

        new_layer: Layer = Layer(self._config_pebble_pihole(container))
        if container.get_plan().services != new_layer.services:
            logger.debug("Service configuration has changed, replan !")
            self.model.unit.status = MaintenanceStatus("Configuring pebble services")
            container.add_layer(self._name, new_layer, combine=True)
            container.replan()

        self.unit.status = ActiveStatus()

    def _on_update_gravity(self, event):
        """ Action that trigger the Pi-hole CLI to update Gravity database """

        logger.info("_on_update_gravity")

        container = self.unit.get_container(self._name)
        results = {
            "success": False,
            "number-of-gravity-domains": 0
        }

        if container.can_connect():
            logging.debug("Executing upgradeGravity command")
            output: str = self._execute_script(container, "pihole", "/usr/local/bin", ["-g"])
            results["success"] = True
            parsed_output = re.search(r"gravity domains: (\w+) \(", output)
            if not parsed_output.groups() or len(parsed_output.groups()) > 1:
                logger.debug(f"Error parsing command output, expected exactly 1 result got {parsed_output.groups()}")
                return event.set_results(results)
            results["number-of-gravity-domains"] = parsed_output.group(1)

        return event.set_results(results)

    def _execute_script(self, container, script: str, path: str, args: list = []):
        logger.debug(f"Running {script} script")
        cmd: list[str] = [f"{path}/{script}"] + args
        process = container.exec(
            cmd,
            environment=self._get_env_config()
        )
        try:
            stdout, _ = process.wait_output()
            logger.debug(f"{script} stdout: {stdout}")
        except ExecError as e:
            logger.error(f"{script} command exited with code {e.exit_code}. Stderr:")
            logger.debug(e.stdout)
            for line in e.stderr.splitlines():
                logger.error(f"    {line}")
            self.model.unit.status = BlockedStatus(f"Error while executing {script}")
            raise
        return stdout

    def _not_running_services(self, services: list) -> tuple:
        not_running_services: tuple = tuple(
            s.name for s in services.values() if not s.is_running()
        )
        logger.debug(f"Non running services: {not_running_services}")
        return not_running_services

    def _config_pebble_pihole(self, container):
        """Generate pebble config for the pihole container"""
        env_config: dict = self._get_env_config()
        return {
            "summary": "Pihole layer",
            "description": "Configure pihole startup",
            "services": {
                "pihole": {
                    "override": "replace",
                    "summary": "pihole-FTL service",
                    "command": "/usr/local/bin/_pihole-FTL.sh",
                    "startup": "enabled",
                    "environment": env_config
                },
                "lighttpd": {
                    "override": "replace",
                    "summary": "Lighttpd service",
                    "command": "/usr/sbin/lighttpd -D -f /etc/lighttpd/lighttpd.conf",
                    "startup": "enabled",
                    "environment": env_config,
                    "after": ["pihole"]
                },
                "cron": {
                    "override": "replace",
                    "summary": "Cron service",
                    "command": "/usr/sbin/cron -f -l 2",
                    "startup": "enabled"
                }
            },
            "checks": {
                "lighttpd-check": {
                    "override": "replace",
                    "http": {
                        "url": f"http://localhost:{self.config['web_port']}"
                    }
                },
                "pihole-check": {
                    "override": "replace",
                    "exec": {
                        "command": "dig +short +norecurse +retry=0 @127.0.0.1 pi.hole || exit 1"
                    }
                }
            }
        }

    def _get_env_config(self) -> dict:
        """Return env config for pihole"""
        return {
            "WEB_PORT": self.config["web_port"],
            "WEBPASSWORD": self.config["web_password"],
            "PIHOLE_DNS_": self._get_upstream_dns_config(),
            # Since pebble exec command doesn't copy the container env,
            # I need to take the required envVars for the application to work properly
            "PH_VERBOSE": "0",
            "PIHOLE_INSTALL": "/etc/.pihole/automated install/basic-install.sh",
            "PHP_ENV_CONFIG": "/etc/lighttpd/conf-enabled/15-fastcgi-php.conf",
            "PHP_ERROR_LOG": "/var/log/lighttpd/error-pihole.log",
            "FTLCONF_LOCAL_IPV4": "0.0.0.0",
            "FTL_CMD": "no-daemon",
            "DNSMASQ_USER": "pihole"
        }

    def _get_upstream_dns_config(self) -> str:
        dns_string = ""
        if self.config["dns1"]:
            dns_string: str = f"{self.config['dns1']}"
        if self.config["dns2"]:
            if dns_string:
                dns_string = f"{dns_string};"
            dns_string = f"{dns_string}{self.config['dns2']}"
        return dns_string

    def _get_script(self, name: str) -> str:
        """Generate the metrics exporter script."""
        file: Path = Path(f"{self._scripts_dir}{name}")
        with file.open() as script:
            return script.read()

if __name__ == "__main__":
    main(PiholeOperatorCharm)
