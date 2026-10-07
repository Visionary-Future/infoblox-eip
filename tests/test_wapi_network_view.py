"""Unit tests for infoblox_wapi_client Network View management methods."""

from unittest.mock import MagicMock

import pytest

from infoblox_wapi_client import InfobloxWAPIClient


@pytest.fixture
def client():
    """Return a client with mocked session for unit tests."""
    c = InfobloxWAPIClient(
        base_url="https://10.0.0.1",
        username="admin",
        password="secret",
    )
    c.session = MagicMock()
    return c


class TestEnsureNetworkView:
    def test_already_exists(self, client):
        client._search_native = MagicMock(return_value=[{"name": "Ali-cn-hangzhou"}])

        result = client.ensure_network_view("Ali-cn-hangzhou")

        client._search_native.assert_called_once_with(
            "networkview", {"name": "Ali-cn-hangzhou"}
        )
        assert result is True

    def test_create_success(self, client):
        client._search_native = MagicMock(return_value=[])
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        client.session.post.return_value = mock_resp

        result = client.ensure_network_view("Ali-cn-hangzhou")

        assert result is True
        client.session.post.assert_called_once()
        call_args = client.session.post.call_args
        assert call_args[1]["json"] == {"name": "Ali-cn-hangzhou"}

    def test_create_400_already_exists(self, client):
        client._search_native = MagicMock(return_value=[])
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "already exists"
        client.session.post.return_value = mock_resp

        result = client.ensure_network_view("Ali-cn-hangzhou")

        assert result is True

    def test_create_400_other_error(self, client):
        client._search_native = MagicMock(return_value=[])
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "invalid name"
        client.session.post.return_value = mock_resp

        result = client.ensure_network_view("bad/name")

        assert result is False

    def test_create_500_error(self, client):
        client._search_native = MagicMock(return_value=[])
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "internal error"
        client.session.post.return_value = mock_resp

        result = client.ensure_network_view("Ali-cn-hangzhou")

        assert result is False

    def test_create_exception(self, client):
        client._search_native = MagicMock(return_value=[])
        client.session.post.side_effect = Exception("timeout")

        result = client.ensure_network_view("Ali-cn-hangzhou")

        assert result is False