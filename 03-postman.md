# Postman MCP Behind the Gateway (Dual OAuth, Dynamic Registration)

This is the easy one. Postman's US MCP server implements the MCP authorization spec properly — authorization-server metadata, PKCE, and dynamic client registration — so it drops into the same **dual OAuth** flow as the [Atlassian backend in the README](README.md#backend-auth-for-a-remote-mcp-server-atlassian) with no app to register, no client id, and no secret to store anywhere.

Its metadata says everything the gateway needs to know:

```bash
curl -s https://mcp.postman.com/.well-known/oauth-authorization-server | jq
```

```json
{
  "issuer": "https://mcp.postman.com",
  "authorization_endpoint": "https://mcp.postman.com/authorize",
  "token_endpoint": "https://mcp.postman.com/token",
  "registration_endpoint": "https://mcp.postman.com/register",
  "revocation_endpoint": "https://mcp.postman.com/revoke",
  "response_types_supported": ["code"],
  "response_modes_supported": ["query"],
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "token_endpoint_auth_methods_supported": ["none", "client_secret_basic", "client_secret_post"],
  "code_challenge_methods_supported": ["S256"]
}
```

`registration_endpoint` is present, so the gateway registers itself, and `"none"` in `token_endpoint_auth_methods_supported` alongside `S256` means it can register as a public client and rely on PKCE. `issuer` equals the host you actually browse to, so `baseUrl` is simply `https://mcp.postman.com`. Note what is *not* there: no `scopes_supported` key at all — not the key set to `null`, but absent — which is why the policy below sends no scopes.

> **Validated up to the browser legs.** On a live kind cluster running `v2026.8.1`, all three manifests apply and report `Accepted`/`Attached`, the gateway returns `401` with a `WWW-Authenticate: Bearer resource_metadata=...` challenge on `/mcp/postman`, its protected-resource and authorization-server documents resolve, and `/oauth-issuer/authorize` redirects to Entra with the controller logging `starting dual OAuth flow` / `auth flow initiated successfully` for `resource_path: /mcp/postman`. The Postman metadata, the three toolset paths, and the `401` behaviour quoted here were read straight off the live service. The two interactive legs — the Entra login and Postman's own authorization page, and therefore the dynamic client registration and the first `tools/call` — still need a human at a browser.

---

## How this fits the demo

Identical in shape to Atlassian — only the upstream party changes:

```
   MCP client                agentgateway                    Entra ID       Postman
       │                          │                              │              │
       │──── connect /mcp/postman ─►│                            │              │
       │◄─── 401 + RFC 9728 metadata ─┤  (points at /oauth-issuer)│              │
       │──── authorize ──────────►│─── leg 1: downstream ───────►│              │
       │                          │◄── id/access token ──────────┤              │
       │                     ┌────┴─────────────────┐                           │
       │                     │  CONSENT SCREEN      │  your branding, Postman's  │
       │                     └────┬─────────────────┘  logo and legal text       │
       │                          │─── leg 2: upstream OAuth (self-registered) ─►│
       │                          │◄── access + refresh token ───────────────────┤
       │◄─── gateway token ───────┤   stored against the user's Entra identity   │
       │──── tools/call ─────────►│─── + Postman token ─────────────────────────►│
```

| | Atlassian (README) | Postman (this guide) |
| --- | --- | --- |
| Upstream client | Dynamic client registration | Dynamic client registration |
| `baseUrl` | `https://cf.mcp.atlassian.com` (≠ the MCP host) | `https://mcp.postman.com` (= the MCP host) |
| Upstream scopes | Explicit Jira/Confluence scopes | **None** — Postman has no scope vocabulary |
| Region caveat | — | OAuth is **US-only**; the EU server is API-key only |

## Region matters

`mcp.eu.postman.com` does not support OAuth — it serves no authorization-server metadata at all (`/.well-known/oauth-authorization-server` returns `404`), so the brokered flow cannot even start against it. If your Postman team is on the EU stack, this guide does not apply — use the auth-only pattern from [`01-dynatrace.md`](01-dynatrace.md) instead, with a Postman API key in the Secret:

```bash
kubectl create secret generic postman-api-key -n agentgateway-system \
  --from-literal=token=${POSTMAN_API_KEY}
```

...and point the backend at `mcp.eu.postman.com`. Everything else in that guide carries over, including the loss of per-user attribution.

---

## Prerequisites

- Steps 1 through 4 of the README's [backend auth section](README.md#backend-auth-for-a-remote-mcp-server-atlassian) already applied: the Entra app usable as an OAuth client, the STS and issuer proxy turned on in the Helm values, `k8s/10-agw-params.yaml`, and `k8s/11-oauth-issuer-route.yaml`.
- [`k8s/07-entra-jwks-backend.yaml`](k8s/07-entra-jwks-backend.yaml) applied.
- A Postman account on the US stack, with access to the workspaces you want to demo.
- The values already exported for the Atlassian section (`ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `GW_ADDRESS`).

Nothing to register on the Postman side, and no Helm changes.

---

## Step 1 — Add the backend, its auth, and the route

```bash
# Remote Postman MCP server + downstream (Entra) authentication + RFC 9728 metadata
envsubst < k8s/21-mcp-postman-backend.yaml | kubectl apply -f -

# Upstream (Postman) OAuth leg + per-backend consent overrides
kubectl apply -f k8s/22-mcp-postman-elicit.yaml

# Expose it at /mcp/postman
kubectl apply -f k8s/23-mcp-postman-httproute.yaml
```

Two choices worth making deliberately:

- **Which toolset.** Postman serves three paths: `/mcp` is the full API surface (~100 tools), `/minimal` is a small curated set, `/code` is the code-generation subset. [`21-mcp-postman-backend.yaml`](k8s/21-mcp-postman-backend.yaml) uses `/mcp`; switch the target `path` to `/minimal` if a client struggles with the full listing, which some do. This is also a cleaner lever than CEL tool filtering when the goal is simply "fewer tools".

- **`baseUrl` alone, and therefore no client id.** The CRD is explicit that these are alternatives — *"set baseUrl alone (for discovery), or both authorizeUrl and accessTokenUrl"* — and that omitting `clientId` is only legal when `baseUrl` or `registrationUrl` is present to register with. Postman satisfies that, so this policy carries no client id and no Secret at all.

- **No `scopes`.** Postman's metadata carries no `scopes_supported` key at all — it has no scope vocabulary, so [`22-mcp-postman-elicit.yaml`](k8s/22-mcp-postman-elicit.yaml) omits the field rather than inventing values. Access is whatever the authorizing Postman user can reach. `refresh_token` is in `grant_types_supported`, so the controller can still refresh the stored token without an `offline_access` request.

## Step 2 — Walk the flow

```bash
kubectl port-forward deployment/agentgateway-proxy -n agentgateway-system 8080:80
npx @modelcontextprotocol/inspector@0.21.2
```

- **Transport:** `Streamable HTTP`
- **URL:** `http://localhost:8080/mcp/postman`
- Click **Connect**.

In order:

1. **Entra login** — the downstream leg.
2. **The gateway consent screen** — your platform name, Postman's logo and legal text from `22-mcp-postman-elicit.yaml`.
3. **Postman's own authorization page** — sign in and allow.

Then the Postman tools list and call. Reconnect and the consent screen is skipped; the grant is cached against your Entra identity until the Postman refresh token expires.

This is the backend to demo first if you want the flow itself to be the story rather than the workarounds — everything upstream is discovered, so the only YAML that matters is the four lines under `chainedAuth.oauth`.

---

## Verification

```bash
# The client's entry point -- must list the gateway itself as the auth server
curl -s http://localhost:8080/.well-known/oauth-protected-resource/mcp/postman | jq

# Elicitation policy accepted
kubectl get enterpriseagentgatewaypolicy mcp-postman-elicit -n agentgateway-system \
  -o jsonpath='{range .status.ancestors[0].conditions[*]}{.type}={.status} reason={.reason} msg={.message}{"\n"}{end}'

# Backend and route accepted
kubectl get agentgatewaybackend mcp-postman -n agentgateway-system -o yaml
kubectl describe httproute mcp-postman -n agentgateway-system
```

Drive `/authorize` directly — a `302` to `login.microsoftonline.com` means both the issuer config and Postman's metadata discovery are sound:

```bash
CID=${ENTRA_CLIENT_ID}
curl -s -D - -o /dev/null "http://localhost:8080/oauth-issuer/authorize\
?response_type=code&client_id=${CID}\
&redirect_uri=http%3A%2F%2Flocalhost%3A9999%2Fcallback&state=probe\
&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM&code_challenge_method=S256\
&resource=http%3A%2F%2Flocalhost%3A8080%2Fmcp%2Fpostman" | grep -iE '^(HTTP|location)'

kubectl logs -n agentgateway-system deploy/enterprise-agentgateway --tail=50 | grep 'auth flow'
```

Confirm the upstream endpoint independently — an unauthenticated call must be a `401`:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://mcp.postman.com/mcp \
  -H 'Content-Type: application/json' -d '{}'
```

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `500 {"error":"server_error"}` from `/oauth-issuer/authorize`, log says `failed to start auth flow ... unknown resource: authorization server metadata request failed with status 404` | `baseUrl` points at a host that serves no authorization-server metadata — the EU host, or a typo. A *trailing slash* is tolerated (`https://mcp.postman.com/` still starts the flow on `v2026.8.1`); a wrong host is not. |
| Dynamic registration fails in the controller log | Postman's `/register` rejected the request. Check the log for the returned `error`; a `clientName` that Postman dislikes or a callback the gateway could not derive are the likely causes. |
| `invalid_scope` from Postman | A `scopes` list was added back into the policy. Remove it — Postman advertises no scopes. |
| The policy reports `Attached=False`, controller logs `AgentgatewayBackend agentgateway-system/mcp-postman is not attached to any Gateway` | Transient, and expected between the second and third `kubectl apply` in Step 1 — the elicitation policy targets a backend that no route references yet. It clears once `23-mcp-postman-httproute.yaml` is applied; re-check the status then. |
| Tools list, but calls return nothing | The authorizing Postman user has no access to the workspace being queried; Postman scopes access to that account, not to the gateway. |
| Huge tool listing overwhelms the client | Switch the target `path` to `/minimal`. |
| `401` at the gateway with a real Entra token | Token `aud`/`iss` mismatch: v1.0 tokens are `https://sts.windows.net/<tenant>/`. See the audience and issuer notes in the README. |

## Cleanup

```bash
kubectl delete -f k8s/23-mcp-postman-httproute.yaml
kubectl delete -f k8s/22-mcp-postman-elicit.yaml
kubectl delete agentgatewaybackend mcp-postman -n agentgateway-system
```

The gateway's self-registered client stays on the Postman side; revoke it from your Postman account's connected-apps settings if you want it gone.

---

## References

- [MCP consent screen](https://docs.solo.io/agentgateway/latest/mcp/token-exchange/elicitations/consent-screen/)
- [MCP elicitations and token exchange](https://docs.solo.io/agentgateway/latest/mcp/token-exchange/elicitations/)
- [Set up a remote Postman MCP server](https://learning.postman.com/docs/reference/postman-api/postman-mcp-server/postman-mcp-remote-server/)
- [OAuth support for the Postman MCP Server](https://postmandeveloperrelations.substack.com/p/oauth-support-for-the-postman-mcp)
- [postmanlabs/postman-mcp-server](https://github.com/postmanlabs/postman-mcp-server)
