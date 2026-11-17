"""The loopback listener must hand its port back when the flow ends.

This is the bug that made Gmail authorisation fail over and over without
ever saying why. `shutdown()` stops serve_forever but leaves the socket
bound, and on Windows a second server may then bind the same port and the
kernel keeps delivering connections to whichever bound FIRST. So every
retry quietly handed the callback to a dead listener from an earlier
attempt, which rejected it for carrying a state it had never issued, and
the live flow sat waiting for a redirect it had already lost. From outside
it looked like Google never redirected at all.
"""

import socket

import pytest

from harvey.integrations import gmail


def port_is_free(port: int) -> bool:
    """Can a fresh listener take this port, without borrowing it?"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


@pytest.fixture
def quick_flow(monkeypatch):
    """A consent flow that gives up immediately and opens no browser."""
    monkeypatch.setattr(gmail, "CALLBACK_TIMEOUT", 0.2)
    monkeypatch.setattr(gmail.webbrowser, "open", lambda *a, **k: True)


def test_the_port_is_free_again_after_a_flow_times_out(quick_flow):
    assert gmail.run_auth_flow("client-id", "secret") is False
    assert port_is_free(gmail.LOOPBACK_PORT), (
        "the loopback port is still held after the flow ended, so the next "
        "attempt will be shadowed by this dead listener")


def test_two_flows_in_a_row_both_get_a_real_listener(quick_flow):
    """The regression. The second attempt has to own the port, not inherit
    a stale socket that answers with the wrong state."""
    assert gmail.run_auth_flow("client-id", "secret") is False
    assert port_is_free(gmail.LOOPBACK_PORT)
    assert gmail.run_auth_flow("client-id", "secret") is False
    assert port_is_free(gmail.LOOPBACK_PORT)


def test_a_flow_refuses_to_start_on_an_occupied_port(quick_flow):
    """If something really is holding the port, say so now rather than
    waiting five minutes for a callback that is going somewhere else."""
    squatter = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    squatter.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        squatter.bind(("127.0.0.1", gmail.LOOPBACK_PORT))
        squatter.listen(1)
        assert gmail.run_auth_flow("client-id", "secret") is False
    finally:
        squatter.close()


def test_the_servers_do_not_borrow_an_address_in_use(quick_flow):
    """allow_reuse_address is what let a retry bind an occupied port and
    then lose every callback to the older socket."""
    import http.server

    captured = {}
    real = http.server.HTTPServer.__init__

    def spy(self, *args, **kwargs):
        captured["allow_reuse"] = type(self).allow_reuse_address
        return real(self, *args, **kwargs)

    http.server.HTTPServer.__init__ = spy
    try:
        gmail.run_auth_flow("client-id", "secret")
    finally:
        http.server.HTTPServer.__init__ = real
    assert captured.get("allow_reuse") is False
