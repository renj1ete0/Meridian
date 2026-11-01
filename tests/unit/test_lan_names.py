"""`.local` names inside containers (`B-250`).

The packets are real mDNS: `REPLY` is what a Windows machine sent on 2026-11-01 (its
address changed to a documentation one), and the compressed reply is how Avahi answers.
"""

from __future__ import annotations

import json
import socket
import struct

import httpx
import pytest

from meridian_core import lanhosts
from meridian_core.lanhosts import LanTransport, lan_url, mdns_names
from worker import lanresolve
from worker.lanresolve import encode_query, one_pass, parse_answers, wanted_names

#: A Windows machine's answer for `OneBox.local`: no question, one A record, TTL 60.
REPLY = (
    b"\x00\x00\x84\x00\x00\x00\x00\x01\x00\x00\x00\x00"
    b"\x06OneBox\x05local\x00\x00\x01\x00\x01\x00\x00\x00<\x00\x04" + socket.inet_aton("192.0.2.22")
)


def compressed_reply(name: str, address: str) -> bytes:
    """A reply echoing the question, with the answer's owner a pointer to it."""
    question = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"
    header = struct.pack("!HHHHHH", 0, 0x8400, 1, 1, 0, 0)
    answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 120, 4) + socket.inet_aton(address)
    return header + question + struct.pack("!HH", 1, 1) + answer


@pytest.fixture
def hosts(tmp_path, monkeypatch):
    path = tmp_path / "hosts.json"
    monkeypatch.setenv("MERIDIAN_LAN_HOSTS_FILE", str(path))
    monkeypatch.setattr(lanhosts, "_cache", None)
    return path


# --------------------------------------------------------------------------
# The packets
# --------------------------------------------------------------------------


def test_the_query_asks_for_an_address_with_a_unicast_answer() -> None:
    packet = encode_query("OneBox.local")
    assert packet[:12] == struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0)
    assert packet[12:] == b"\x06OneBox\x05local\x00" + struct.pack("!HH", 1, 0x8001)


@pytest.mark.parametrize("name", ["", "a..local", "x" * 64 + ".local"])
def test_a_name_dns_cannot_carry_is_refused(name) -> None:
    with pytest.raises(ValueError):
        encode_query(name)


def test_a_windows_reply_gives_its_address() -> None:
    assert parse_answers(REPLY, "onebox.local") == ["192.0.2.22"]


def test_a_compressed_reply_gives_its_address() -> None:
    assert parse_answers(compressed_reply("box.local", "192.0.2.9"), "box.local") == ["192.0.2.9"]


def test_an_answer_about_another_machine_is_not_taken() -> None:
    """mDNS is a shared channel: every host's answers arrive."""
    assert parse_answers(REPLY, "other.local") == []


def test_another_hosts_question_is_not_an_answer() -> None:
    assert parse_answers(encode_query("onebox.local"), "onebox.local") == []


@pytest.mark.parametrize(
    "packet",
    [
        b"\x00" * 5,  # shorter than a header
        REPLY[:-3],  # truncated record
        struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0) + b"\xc0\x0c",  # pointer to itself
        struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0) + b"\x3f",  # label past the end
    ],
)
def test_a_malformed_packet_is_refused_not_misread(packet) -> None:
    with pytest.raises(ValueError):
        parse_answers(packet, "onebox.local")


# --------------------------------------------------------------------------
# The resolver's pass
# --------------------------------------------------------------------------


def test_only_local_names_in_the_url_settings_are_asked_for() -> None:
    env = {
        "MERIDIAN_EMBEDDER_URL": "http://OneBox.local:8101",
        "LOCAL_LLM_URL": "http://192.0.2.5:8080/v1",
        "LOCAL_CHAT_LLM_URL": "http://gpu.lan:8080/v1",
        "MERIDIAN_LAN_NAMES": " extra.local ,",
        "DATABASE_URL": "postgresql://db.local/x",  # not a URL setting it reads
    }
    assert wanted_names(env) == {"onebox.local", "extra.local"}


def test_every_url_setting_a_client_reads_is_watched() -> None:
    """Drift: a client setting that can hold a `.local` URL and is not watched fails
    as "name not known", exactly like a machine that is off."""
    from meridian_core.embedder import RemoteEmbedder

    with open(RemoteEmbedder.from_env.__code__.co_filename) as fh:
        source = fh.read()
    for var in ("MERIDIAN_EMBEDDER_URL", "MERIDIAN_RERANKER_URL"):
        assert var in source
        assert var in lanhosts.URL_VARIABLES


def test_a_pass_writes_what_answered(hosts) -> None:
    entries = one_pass({"onebox.local"}, str(hosts), resolver=lambda n: "192.0.2.22")
    assert entries["onebox.local"]["address"] == "192.0.2.22"
    assert json.loads(hosts.read_text()) == entries


def test_a_machine_that_stops_answering_keeps_its_last_address(hosts) -> None:
    """Off, probably: its clients' connect timeouts say so. Forgetting the address would
    turn that into "name not known" and lose it for when it returns."""
    one_pass({"onebox.local"}, str(hosts), resolver=lambda n: "192.0.2.22")
    entries = one_pass({"onebox.local"}, str(hosts), resolver=lambda n: None)
    assert entries["onebox.local"]["address"] == "192.0.2.22"


def test_a_name_no_longer_configured_is_dropped(hosts) -> None:
    one_pass({"old.local", "onebox.local"}, str(hosts), resolver=lambda n: "192.0.2.1")
    entries = one_pass({"onebox.local"}, str(hosts), resolver=lambda n: "192.0.2.1")
    assert set(entries) == {"onebox.local"}


def test_a_moved_machine_is_followed(hosts) -> None:
    one_pass({"onebox.local"}, str(hosts), resolver=lambda n: "192.0.2.22")
    assert lan_url("http://onebox.local:8101") == "http://192.0.2.22:8101"
    one_pass({"onebox.local"}, str(hosts), resolver=lambda n: "192.0.2.23")
    lanhosts._cache = None  # the same mtime second on a fast filesystem
    assert lan_url("http://onebox.local:8101") == "http://192.0.2.23:8101"


def test_once_prints_and_exits(hosts, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MERIDIAN_EMBEDDER_URL", "http://OneBox.local:8101")
    monkeypatch.setattr(lanresolve, "resolve", lambda n, *a: "192.0.2.22")
    assert lanresolve.main(["--once"]) == 0
    assert "192.0.2.22" in capsys.readouterr().out


# --------------------------------------------------------------------------
# The clients' side
# --------------------------------------------------------------------------


def test_names_are_found_in_urls() -> None:
    assert mdns_names(["http://A.Local:1/x", "http://b.lan", "", "https://c.local."]) == {
        "a.local",
        "c.local",
    }


def test_a_known_name_becomes_its_address_keeping_port_path_and_credentials(hosts) -> None:
    hosts.write_text(json.dumps({"onebox.local": {"address": "192.0.2.22"}}))
    assert lan_url("http://OneBox.local:8101/v1/embeddings?x=1") == (
        "http://192.0.2.22:8101/v1/embeddings?x=1"
    )
    assert lan_url("http://u:p@onebox.local/v1") == "http://u:p@192.0.2.22/v1"


def test_anything_else_is_left_for_the_system(hosts) -> None:
    hosts.write_text(json.dumps({"onebox.local": {"address": "192.0.2.22"}}))
    for url in ("http://other.local:1", "http://onebox.lan:1", "http://192.0.2.1:1"):
        assert lan_url(url) == url


def test_no_file_or_a_broken_one_changes_nothing(hosts) -> None:
    assert lan_url("http://onebox.local:1") == "http://onebox.local:1"
    hosts.write_text("{not json")
    assert lan_url("http://onebox.local:1") == "http://onebox.local:1"
    hosts.write_text(json.dumps({"onebox.local": {"address": 7}}))
    assert lan_url("http://onebox.local:1") == "http://onebox.local:1"


async def test_the_transport_sends_to_the_address_and_keeps_the_name_as_host(hosts) -> None:
    hosts.write_text(json.dumps({"onebox.local": {"address": "192.0.2.22"}}))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=LanTransport(httpx.MockTransport(handler))) as c:
        await c.get("http://onebox.local:8101/health")
    assert seen[0].url.host == "192.0.2.22" and seen[0].url.port == 8101
    assert seen[0].headers["host"] == "onebox.local:8101"


def test_the_embedder_client_it_builds_uses_the_transport() -> None:
    from meridian_core.embedder import RemoteEmbedder

    client = RemoteEmbedder("http://onebox.local:8101")._http()
    assert isinstance(client._transport, LanTransport)


async def test_the_local_model_call_goes_to_the_address(hosts, monkeypatch) -> None:
    from types import SimpleNamespace

    from meridian_core import provider

    hosts.write_text(json.dumps({"onebox.local": {"address": "192.0.2.22"}}))
    seen: list[str] = []
    real = httpx.AsyncClient

    class Routed(real):
        def __init__(self, *args, **kwargs):
            def handler(request: httpx.Request) -> httpx.Response:
                seen.append(str(request.url))
                return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

            super().__init__(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Routed)
    agent = SimpleNamespace(
        agent_id="local",
        endpoint="http://onebox.local:8080/v1",
        model="m",
        api_key_env_var=None,
        provider="openai_compatible",
    )
    text, *_ = await provider._call_openai_compatible(
        agent, prompt="p", system=None, max_tokens=5, timeout_s=10
    )
    assert text == "ok"
    assert seen == ["http://192.0.2.22:8080/v1/chat/completions"]
