"""Unit tests for config.py — precedence resolution."""

from config import _resolve


class TestResolve:
    def test_cli_wins(self):
        assert _resolve("cli-value", "config-value", "env-value", "default") == "cli-value"

    def test_config_wins_when_cli_none(self):
        assert _resolve(None, "config-value", "env-value", "default") == "config-value"

    def test_env_wins_when_cli_config_none(self):
        assert _resolve(None, None, "env-value", "default") == "env-value"

    def test_default_when_all_none(self):
        assert _resolve(None, None, None, "default") == "default"

    def test_network_view_prefix_defaults_to_Ali(self):
        assert _resolve(None, None, None, "Ali") == "Ali"

    def test_network_view_prefix_cli_overrides(self):
        assert _resolve("MyPrefix", None, None, "Ali") == "MyPrefix"

    def test_network_view_prefix_config_overrides(self):
        assert _resolve(None, "CorpPrefix", None, "Ali") == "CorpPrefix"

    def test_first_non_none_stops_at_cli(self):
        assert _resolve("CLI", "Config", "Env", "Default") == "CLI"

    def test_false_is_valid_value(self):
        assert _resolve(False, None, None, True) is False

    def test_empty_string_is_valid_value(self):
        assert _resolve("", "config", None, "default") == ""