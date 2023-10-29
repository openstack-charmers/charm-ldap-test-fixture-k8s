#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

    https://discourse.charmhub.io/t/4208
"""

import logging
import socket

import jinja2
from ops.charm import ActionEvent, CharmBase
from ops.main import main
from ops.model import ActiveStatus

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class CharmLdapTestFixtureK8SCharm(CharmBase):
    """Charm the service."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(
            self.on.phpldapadmin_pebble_ready, self._on_phpldapadmin_pebble_ready
        )
        self.framework.observe(
            self.on.get_ldap_url_action,
            self._get_ldap_url_action,
        )

    def _get_ldap_url_action(self, event: ActionEvent) -> None:
        # This charm has no relations so it has no network
        # bindings so cannot get ip through ops framework
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        event.set_results({"url": "ldap://{}".format(ip)})

    def configure_slap_pkg(self, container):
        """Configure  slapd in container."""
        domain = self.config["domain"]
        slap_settings = [
            "slapd slapd/internal/adminpw password crapper",
            "slapd slapd/password1 password crapper",
            "slapd slapd/password2 password crapper",
            f"slapd slapd/domain string {domain}",
            "slapd shared/organization string test",
        ]
        process = container.exec(["debconf-set-selections"])
        for line in slap_settings:
            process.stdin.write(line + "\n")
            process.stdin.flush()
        process.stdin.close()
        process.wait()
        process = container.exec(["dpkg-reconfigure", "-f", "noninteractive", "slapd"])
        process.wait_output()

    @property
    def dc(self):
        """Return dc for config."""
        return ",".join([f"dc={d}" for d in self.config["domain"].split(".")])

    def setup_slap_users(self, container):
        """Add test users to slapd in container."""
        gid_counter = 500
        uid_counter = 1000
        context = {"dc": self.dc}
        groups = []
        users = []
        for group in ["admin", "openstack"]:
            groups.append({"gidnumber": gid_counter, "cn": group})
            gid_counter += 1

        user_gid = groups[0]["gidnumber"]
        for user in self.config["users"].split(","):
            name = user.replace(" ", "").lower()
            users.append(
                {
                    "uidnumber": uid_counter,
                    "uid": name,
                    "sn": user,
                    "first": user.split()[0],
                    "gidnumber": user_gid,
                }
            )
            uid_counter += 1

        context["groups"] = groups
        context["users"] = users
        loader = jinja2.FileSystemLoader("src/templates")
        _tmpl_env = jinja2.Environment(loader=loader)
        template = _tmpl_env.get_template("setup.ldif.j2")
        container.push("/tmp/setup.ldif", template.render(context))
        process = container.exec(["slapadd", "-v", "-c", "-l", "/tmp/setup.ldif"])
        process.wait()

    def setup_php(self, container):
        """Configure phpldapadmin in container."""
        current_contents = container.pull("/etc/phpldapadmin/config.php")
        new_contents = ""
        for line in current_contents:
            new_contents += line.replace("dc=example,dc=com", self.dc)
        container.push("/etc/phpldapadmin/config.php", new_contents)

    def setup(self):
        """Configure container."""
        container = self.unit.get_container("phpldapadmin")
        self.configure_slap_pkg(container)
        self.setup_php(container)
        self.setup_slap_users(container)
        container.restart("phpldapadmin", "slapd")

    def _on_phpldapadmin_pebble_ready(self, event):
        """Define and start a workload using the Pebble API.

        Change this example to suit your needs. You'll need to specify the right entrypoint and
        environment configuration for your specific workload.

        Learn more about interacting with Pebble at at https://juju.is/docs/sdk/pebble.
        """
        # Get a reference the container attribute on the PebbleReadyEvent
        container = event.workload
        # Add initial Pebble config layer using the Pebble API
        container.add_layer("phpldapadmin", self._pebble_layer, combine=True)
        # Make Pebble reevaluate its plan, ensuring any services are started if enabled.
        container.replan()
        # Learn more about statuses in the SDK docs:
        # https://juju.is/docs/sdk/constructs#heading--statuses
        self.setup()
        self.unit.status = ActiveStatus()

    @property
    def _pebble_layer(self):
        """Return a dictionary representing a Pebble layer."""
        return {
            "summary": "phpldapadmin layer",
            "description": "pebble config layer for phpldapadmin",
            "services": {
                "phpldapadmin": {
                    "override": "replace",
                    "summary": "phpldapadmin",
                    "command": "/usr/sbin/apache2ctl -DFOREGROUND",
                    "startup": "enabled",
                },
                "slapd": {
                    "override": "replace",
                    "summary": "slapd",
                    "command": "/usr/sbin/slapd -d 0",
                    "startup": "enabled",
                },
            },
        }


if __name__ == "__main__":  # pragma: nocover
    main(CharmLdapTestFixtureK8SCharm)
