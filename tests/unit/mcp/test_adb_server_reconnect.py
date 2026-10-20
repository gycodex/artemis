"""Reconnect behavior for explicitly bound TCP Android devices."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from artemis.mcp.adb_server import _restore_configured_tcp_device


def test_configured_tcp_device_is_reconnected_when_missing():
    adb = MagicMock()
    restored = SimpleNamespace(serial="127.0.0.1:7555")
    adb.device_list.return_value = [restored]

    devices = _restore_configured_tcp_device(adb, "127.0.0.1:7555", [])

    adb.connect.assert_called_once_with("127.0.0.1:7555", timeout=3.0)
    assert devices == [restored]


def test_usb_serial_is_not_treated_as_tcp_endpoint():
    adb = MagicMock()

    devices = _restore_configured_tcp_device(adb, "63191FDKX00062", [])

    adb.connect.assert_not_called()
    assert devices == []
