import unittest

from cztvhub.config import DvbConfig
from cztvhub.dvb import tvheadend as tvh
from cztvhub.dvb.setup import plan_setup, run_setup
from dvb_fixtures import CT1, NOVA, load


class SetupClient:
    def __init__(self, configs=None, autorecs=None):
        self.configs = configs if configs is not None else load("dvr_config_grid.json")["entries"]
        self.rules = tvh.parse_autorecs(autorecs if autorecs is not None else load("autorec_grid.json"))
        self.saved, self.created, self.deleted = [], [], []

    def dvr_configs(self):
        return self.configs

    def channels(self):
        return tvh.parse_channels(load("channel_grid.json"))

    def autorecs(self):
        return self.rules

    def save_node(self, node):
        self.saved.append(node)

    def create_autorec(self, conf):
        self.created.append(conf)

    def delete_nodes(self, uuids):
        self.deleted.extend(uuids)


class SetupTest(unittest.TestCase):
    def test_all_channels(self):
        client = SetupClient()
        run_setup(client, DvbConfig(retention_days=3), out=lambda m: None)
        self.assertEqual(client.saved, [{"uuid": "c0c1c2c3", "retention-days": 3, "removal-days": 3}])
        # CT1 already has a cztvhub rule; Nova and Prima get one; the manual rule is untouched
        self.assertEqual([c["channel"] for c in client.created], [NOVA, "abcdefabcdefabcdefabcdefabcdef12"])
        conf = client.created[0]
        self.assertEqual((conf["title"], conf["comment"], conf["config_name"], conf["enabled"]),
                         (".", "cztvhub", "c0c1c2c3", True))
        self.assertEqual(client.deleted, ["b0b1b2b3"])  # rule of a channel that no longer exists

    def test_selected_channels_and_unknown_names(self):
        client = SetupClient()
        plan = plan_setup(client, DvbConfig(record_channels=["čt1 hd", "Missing"]))
        self.assertEqual(plan.create, [])
        self.assertEqual(plan.unknown_channels, ["Missing"])
        self.assertEqual(plan.delete, ["b0b1b2b3"])

    def test_idempotent(self):
        client = SetupClient(configs=[{"uuid": "c", "name": "", "retention-days": 3, "removal-days": 3}],
                             autorecs={"entries": [{"uuid": "r1", "channel": CT1, "comment": "cztvhub"}]})
        plan = run_setup(client, DvbConfig(record_channels=["ČT1 HD"]), out=lambda m: None)
        self.assertEqual((plan.config_update, plan.create, plan.delete), ({}, [], []))
        self.assertEqual((client.saved, client.created, client.deleted), ([], [], []))

    def test_duplicate_rules_removed(self):
        rules = {"entries": [{"uuid": "r1", "channel": CT1, "comment": "cztvhub"},
                             {"uuid": "r2", "channel": CT1, "comment": "cztvhub"}]}
        plan = plan_setup(SetupClient(autorecs=rules), DvbConfig(record_channels=["ČT1 HD"]))
        self.assertEqual(plan.delete, ["r2"])

    def test_dry_run_changes_nothing(self):
        client = SetupClient()
        lines = []
        run_setup(client, DvbConfig(), dry_run=True, out=lines.append)
        self.assertEqual((client.saved, client.created, client.deleted), ([], [], []))
        self.assertTrue(any("Nova HD" in line for line in lines))

    def test_tvh_days_round_up_to_listed_values(self):
        from cztvhub.dvb.setup import tvh_days
        self.assertEqual([tvh_days(d) for d in (1, 2, 3, 4, 6, 7, 8, 14)], [1, 3, 3, 5, 7, 7, 14, 14])
        client = SetupClient()
        run_setup(client, DvbConfig(retention_days=4), out=lambda m: None)
        self.assertEqual(client.saved[0]["removal-days"], 5)

    def test_no_dvr_profile(self):
        with self.assertRaises(RuntimeError):
            plan_setup(SetupClient(configs=[]), DvbConfig())
