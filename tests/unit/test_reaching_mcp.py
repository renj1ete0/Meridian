"""How an assistant reaches `/mcp` (task `B-138`, ADR 0003).

Two ways in had silently gone nowhere: nginx served the single-page app for `/mcp`, and the
only published path was the tunnel. These hold the fixes.
"""

from __future__ import annotations

import pathlib
import re

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
NGINX = (REPO / "web/nginx.conf").read_text()
OVERRIDE = REPO / "deploy/lan/publish-web.yml"
COMPOSE = yaml.safe_load((REPO / "docker-compose.yml").read_text())


class _ComposeLoader(yaml.SafeLoader):
    """Reads Compose's `!override` tag as the plain value it carries."""


def _plain(loader: yaml.SafeLoader, node: yaml.Node) -> object:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


_ComposeLoader.add_constructor("!override", _plain)


def _override() -> dict:
    return yaml.load(OVERRIDE.read_text(), Loader=_ComposeLoader)  # noqa: S506 - safe subclass


def _block(path: str) -> str:
    match = re.search(r"location " + re.escape(path) + r" \{(.*?)\n    \}", NGINX, re.S)
    assert match, f"nginx has no `location {path}` block"
    return match.group(1)


def test_nginx_sends_mcp_to_the_api_not_to_the_app() -> None:
    assert "proxy_pass http://api:8000;" in _block("/mcp")


def test_mcp_responses_are_streamed_not_buffered() -> None:
    """Streamable HTTP answers with server-sent events; a buffered proxy holds them back."""
    assert "proxy_buffering off;" in _block("/mcp")


def test_the_mcp_block_comes_before_the_catch_all() -> None:
    assert NGINX.index("location /mcp") < NGINX.index("location / {")


def test_publishing_puts_web_on_a_network_with_a_gateway() -> None:
    """`B-18`: a port on a container that is only on an `internal` network publishes nothing."""
    web = _override()["services"]["web"]
    networks = web["networks"]
    assert web["ports"]
    assert any(not COMPOSE["networks"][name].get("internal") for name in networks)


def test_publishing_is_local_only_unless_asked() -> None:
    port = _override()["services"]["web"]["ports"][0]
    assert port.startswith("${WEB_BIND:-127.0.0.1}:")


def test_the_port_published_is_the_one_nginx_listens_on() -> None:
    listen = re.search(r"listen (\d+);", NGINX).group(1)
    port = _override()["services"]["web"]["ports"][0]
    assert port.endswith(f":{listen}")
