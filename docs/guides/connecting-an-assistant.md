# Connecting an assistant over MCP

Let Claude (Code or Desktop), Gemini CLI or any MCP client answer questions from the corpus,
with citations, using its own model and your own subscription. Meridian serves read-only tools
at `/mcp` (see [features/mcp.md](../features/mcp.md)); this guide gets a client connected.
The decision behind it is [ADR 0003](../adr/0003-external-assistants-over-mcp.md).

## 1. Decide where the assistant runs

| The assistant runs… | It connects to | You need |
|---|---|---|
| On the server itself | `http://localhost:8080/mcp` | Step 2 with the default `WEB_BIND` |
| On a laptop on the same LAN or VPN (e.g. Tailscale) | `http://<server-ip>:8080/mcp` | Step 2 with `WEB_BIND=<server LAN IP>`, and step 3 |
| On claude.ai (web or phone) | `https://<your-hostname>/mcp` | The Cloudflare tunnel, which compose already runs |

No router port forwarding is needed in the first two cases.

## 2. Publish the web front door (server or LAN only)

Production publishes no port; the tunnel is the only way in. To reach `/mcp` from the server or
the LAN, publish `web`, which proxies `/mcp` to the API:

```bash
# this server only
docker compose -f docker-compose.yml -f deploy/lan/publish-web.yml up -d web
# or, for the LAN: in .env set WEB_BIND=192.168.1.5 (the server's LAN address), then the same
```

`WEB_PORT` changes the port (default 8080). Firewall it to the machines that should reach it.

## 3. Tell the MCP transport which host names to answer (LAN and tunnel)

The transport refuses requests whose `Host` it does not recognise (DNS-rebinding protection).
Unset, it answers only `localhost`. For anything else, in `.env`:

```bash
MERIDIAN_MCP_ALLOWED_HOSTS=192.168.1.5:8080            # or your tunnel hostname
MERIDIAN_MCP_ALLOWED_ORIGINS=http://192.168.1.5:8080   # or https://<your-hostname>
```

For the tunnel, also set `MERIDIAN_MCP_RESOURCE_URL=https://<your-hostname>/mcp` so issued
tokens print the right address. Then `docker compose up -d --no-deps api`.

## 4. Issue a token

The simplest way is **Admin → Assistant access**: name the device, pick a profile and an
expiry, and press *Issue token*. The token and the setup for each client are shown once. The
same page lists and revokes tokens. Open Admin on the address the assistant will use, since
that is the address the setup is written for.

From a shell, the same:

```bash
docker compose exec api python -m api.tokens issue laptop-claude-code --profile reader
```

- `--profile reader` (default) can search, read source details, list what is new and see the
  overview, and read what the site shows: nodes and routes in the graph, a term's
  neighbourhood, map areas, gaps, contested claims and growth. `analyst` and `operator` add
  read-only SQL. No profile can write.
- `--days 90` (default) sets the expiry; `--days 0` never expires (revoke it when done).
- The token is printed **once**, with ready-to-paste setup for Claude Code and Gemini CLI.
  Only its hash is stored.

Anyone holding a token who can reach the URL can read the whole corpus until it expires or is
revoked. Issue one per device or person, so you can revoke one without the others.

```bash
docker compose exec api python -m api.tokens list           # never shows secrets
docker compose exec api python -m api.tokens revoke 12
```

## 5. Add it to the client

**Claude Code**

```bash
claude mcp add --transport http meridian http://localhost:8080/mcp \
  --header "Authorization: Bearer <token>"
```

**Gemini CLI**, in `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "meridian": {
      "httpUrl": "http://localhost:8080/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

**Other clients** that support streamable HTTP with a custom header take the same URL and
header. A client that can only launch a local process can use a bridge such as `mcp-remote`
with `--header "Authorization: Bearer <token>"`.

## 6. Check it

Ask the assistant something the corpus covers, and check that the answer cites passages.
If the client reports `401`, the token is wrong, expired or revoked. A `421` or "invalid host"
means step 3. If the client receives an HTML page, nginx is an old image without the `/mcp`
route: rebuild `web`.
