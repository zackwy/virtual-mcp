# GitHub MCP Behind the Gateway (Dual OAuth, Static Client)

This guide adds GitHub's hosted MCP server to the demo using the same **dual OAuth** flow as the [Atlassian backend in the README](README.md#backend-auth-for-a-remote-mcp-server-atlassian): Entra login, gateway-hosted consent screen, then an upstream OAuth leg whose token the gateway stores per user and attaches to every call. The MCP client never sees a GitHub credential.

One thing differs, and it drives the whole guide: **GitHub does not support dynamic client registration.** Atlassian let the gateway register itself, so the README needed no Atlassian app. Here you register a GitHub App by hand and hand its client id and secret to the gateway.

You can see that in GitHub's own metadata. The document is at the RFC 8414 path-inserted location, not under the issuer path:

```bash
curl -s https://github.com/.well-known/oauth-authorization-server/login/oauth | jq
```

```json
{
  "issuer": "https://github.com/login/oauth",
  "authorization_endpoint": "https://github.com/login/oauth/authorize",
  "token_endpoint": "https://github.com/login/oauth/access_token",
  "grant_types_supported": ["authorization_code", "refresh_token", "urn:ietf:params:oauth:grant-type:device_code"],
  "code_challenge_methods_supported": ["S256"],
  "scopes_supported": ["offline_access"]
}
```

No `registration_endpoint`. That single omission is the difference between this guide and the Atlassian one.

> **Validated up to the browser.** On a live `kind` cluster running `v2026.8.1`, the manifests apply cleanly, the backend and route report `Accepted`, the elicitation policy reports `Accepted`/`Attached`, and `/oauth-issuer/authorize` with `resource=…/mcp/github` returns a `302` to Entra with `dual OAuth flow initiated` in the controller log. The three interactive legs — Entra login, the consent screen, GitHub's authorize page — still have to be walked by hand. The GitHub-side facts below were read straight off GitHub's live metadata and MCP endpoints.

---

## How this fits the demo

```
   MCP client                agentgateway                    Entra ID       GitHub
       │                          │                              │             │
       │──── connect /mcp/github ─►│                             │             │
       │◄─── 401 + RFC 9728 metadata ─┤  (points at /oauth-issuer)│             │
       │                          │                              │             │
       │──── authorize ──────────►│─── leg 1: downstream ───────►│             │
       │                          │◄── id/access token ──────────┤             │
       │                     ┌────┴─────────────────┐                          │
       │                     │  CONSENT SCREEN      │  your branding, GitHub's  │
       │                     │  "Allow / Deny"      │  logo and legal text      │
       │                     └────┬─────────────────┘                          │
       │                          │─── leg 2: upstream OAuth ─────────────────►│
       │                          │◄── user token + refresh token ─────────────┤
       │◄─── gateway token ───────┤   stored against the user's Entra identity │
       │                          │                                            │
       │──── tools/call ─────────►│─── + GitHub user token ───────────────────►│
```

Compared with the Atlassian backend:

|                       | Atlassian (README)                   | GitHub (this guide)                                   |
| --------------------- | ------------------------------------ | ----------------------------------------------------- |
| Upstream client       | Dynamic client registration          | **A GitHub App you register**                         |
| `chainedAuth.oauth`   | `baseUrl` discovery                  | Explicit `authorizeUrl` + `accessTokenUrl` + `clientId` + `clientSecretRef` |
| Upstream scopes       | `read:jira-work`, … , `offline_access` | Ignored by GitHub Apps — permissions come from the app registration |
| Upstream token life   | Per Atlassian's grant                | 8 hours, refreshable for 6 months (GitHub App, if token expiry is enabled) |
| Consent screen        | Yes                                  | Yes                                                   |

## GitHub App or OAuth App?

Both work; the App is the better demo and the better production story.

| | GitHub App (recommended) | OAuth App |
| --- | --- | --- |
| Permission model | Fine-grained, per-repository, chosen at install | Coarse classic scopes (`repo` is all-or-nothing) |
| Token lifetime | 8h user token + refresh token, *if* "Expire user authorization tokens" is on | Never expires, no refresh token |
| Gateway refresh | Works — the controller refreshes at <10% of remaining lifetime | Breaks: no `expires_in` and no refresh token, so the controller assumes a 1h lifetime and re-elicits hourly |
| Blast radius | Only the repos the app is installed on | Everything the user can reach |

The refresh row is the one that bites. GitHub OAuth Apps return neither `expires_in` nor a refresh token, and the controller "assumes a lifetime of one hour" when a provider omits `expires_in` — so it tries to refresh at roughly six minutes remaining, finds nothing to refresh with, and "resets the elicitation to a pending state," sending the user back through consent. The token itself never expires; the *session* still ends every hour. A GitHub App with token expiry enabled gives an 8-hour token that renews silently instead.

An OAuth App's non-expiring token is also a credential sitting in the STS with no natural end, which is exactly what this architecture is supposed to avoid. The manifests assume a GitHub App.

---

## Prerequisites

- Steps 1 through 4 of the README's [backend auth section](README.md#backend-auth-for-a-remote-mcp-server-atlassian) already applied: the Entra app usable as an OAuth client, the STS and issuer proxy turned on in the Helm values, `k8s/10-agw-params.yaml`, and `k8s/11-oauth-issuer-route.yaml`.
- [`k8s/07-entra-jwks-backend.yaml`](k8s/07-entra-jwks-backend.yaml) applied.
- Rights to create a GitHub App in your user account or organization, and to install it.
- The values already exported for the Atlassian section (`ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `GW_ADDRESS`).

The Helm values do not change.

---

## Step 1 — Register the GitHub App

Create the app at **Settings → Developer settings → GitHub Apps → New GitHub App** (or the same path under an organization's settings).

1. **Homepage URL** — anything; `http://${GW_ADDRESS}` is fine.
2. **Callback URL** — the gateway's *upstream* OAuth callback:

   ```
   http://localhost:8080/oauth-issuer/callback/upstream
   ```

   The README registers `http://localhost:8080/oauth-issuer/callback/downstream` in Entra for leg 1; this is the leg 2 counterpart. Both are `gateway_config.base_url` from the Helm values plus a fixed suffix — the controller carries `/callback/downstream` and `/callback/upstream` as adjacent constants, and you can watch it compose the downstream one live:

   ```bash
   curl -s -D - -o /dev/null "http://localhost:8080/oauth-issuer/authorize?response_type=code&client_id=${ENTRA_CLIENT_ID}&redirect_uri=http%3A%2F%2Flocalhost%3A9999%2Fcallback&state=probe&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM&code_challenge_method=S256&resource=http%3A%2F%2Flocalhost%3A8080%2Fmcp%2Fgithub" | grep -i location
   # ...&redirect_uri=http%3A%2F%2Flocalhost%3A8080%2Foauth-issuer%2Fcallback%2Fdownstream&...
   ```

   **The port must match `base_url` exactly.** GitHub compares the callback URL literally, so registering `localhost:18080` while the gateway sends `localhost:8080` fails the flow. If you change `GW_ADDRESS`, you change it in the Helm values, in Entra, in the port-forward, and here. GitHub allows plain `http` only for `localhost`/`127.0.0.1`; a load-balancer address must be HTTPS.
3. **Expire user authorization tokens** — leave **enabled**. This is what makes GitHub issue a refresh token, which is what lets the gateway keep the session alive without sending the user back through the browser every 8 hours.
4. **Webhook** — uncheck **Active**. Nothing here needs webhooks.
5. **Permissions** — grant read-only to start: `Contents: Read`, `Metadata: Read`, `Issues: Read`, `Pull requests: Read`. Add write permissions only for tools you actually intend to call.
6. **Where can this app be installed** — your choice; "Only on this account" is fine for a demo.

Then:

- **Generate a client secret** and copy the value.
- **Install the app** (**Install App** in its sidebar) on the account or org, choosing the repositories it may see. Without an installation the app has a valid token and no access, which surfaces as empty tool results rather than an auth error.

```bash
export GITHUB_CLIENT_ID=Iv23li...          # "Client ID" on the app's General page (not the App ID)
export GITHUB_CLIENT_SECRET=<the generated client secret>
```

> Use the **Client ID** (`Iv23li…`), not the numeric **App ID**. They sit next to each other on the same page and the OAuth endpoints reject the App ID.

## Step 2 — Store the client secret

```bash
kubectl create secret generic github-oauth-client -n agentgateway-system \
  --from-literal=client_secret=${GITHUB_CLIENT_SECRET}
```

## Step 3 — Add the backend, its auth, and the route

```bash
# Remote GitHub MCP server + downstream (Entra) authentication + RFC 9728 metadata
envsubst < k8s/18-mcp-github-backend.yaml | kubectl apply -f -

# Upstream (GitHub) OAuth leg + per-backend consent overrides
envsubst < k8s/19-mcp-github-elicit.yaml | kubectl apply -f -

# Expose it at /mcp/github
kubectl apply -f k8s/20-mcp-github-httproute.yaml
```

Two details in [`18-mcp-github-backend.yaml`](k8s/18-mcp-github-backend.yaml) worth knowing:

- **The upstream host is `api.githubcopilot.com`, path `/mcp/`.** That host serves the MCP endpoint for ordinary GitHub tokens; the name is where it is hosted, not a statement about licensing. Its protected-resource metadata confirms the resource:

  ```bash
  curl -s https://api.githubcopilot.com/.well-known/oauth-protected-resource/mcp | jq -r .resource
  # https://api.githubcopilot.com/mcp
  ```

- **Narrower endpoints exist.** `/readonly` serves the same toolset with mutating tools removed, and `/x/<toolset>` (e.g. `/x/repos`, `/x/issues`) serves one toolset. Switching the target `path` is a cleaner way to limit exposure than filtering ~90 tools at the gateway — though the CEL filtering in [`k8s/09-mcp-tool-authz.yaml`](k8s/09-mcp-tool-authz.yaml) still applies if you want per-role differences on top.

And in [`19-mcp-github-elicit.yaml`](k8s/19-mcp-github-elicit.yaml):

- **`authorizeUrl` and `accessTokenUrl` are set explicitly, with no `baseUrl`.** The CRD allows one or the other, never a mix — *"set baseUrl alone (for discovery), or both authorizeUrl and accessTokenUrl"* — and a third rule spells out why the explicit form needs a client id: *"omitting clientId requires baseUrl or registrationUrl for dynamic client registration."* Discovery would technically work here (the metadata exists) but buys nothing without a `registration_endpoint`, and pointing `baseUrl` at `https://github.com` would fail the issuer check the way `mcp.atlassian.com` does — GitHub declares its issuer as `https://github.com/login/oauth`.
- **`scopes: [offline_access]`.** GitHub Apps ignore the `scope` parameter entirely; permissions come from the app registration and its installation. `offline_access` is the only scope the *authorization server* metadata advertises, and it is kept here for that reason — but on a GitHub App the refresh token comes from the app's "Expire user authorization tokens" setting, not from this list. Note that the *protected resource* metadata at `api.githubcopilot.com` advertises thirteen classic scopes (`repo`, `read:org`, `read:user`, `workflow`, `gist`, …); those are the OAuth App vocabulary, and they are what you would put here if you used an OAuth App instead.

## Step 4 — Walk the flow

```bash
kubectl port-forward deployment/agentgateway-proxy -n agentgateway-system 8080:80
npx @modelcontextprotocol/inspector@0.21.2
```

> If the Inspector reports `Failed to connect … fetch failed` after a few milliseconds, that is the port-forward, not the auth flow — a real auth failure comes back as a `401` with a `WWW-Authenticate` header, not a transport error. `port-forward deployment/...` binds to one pod and does not follow a rollout, so applying `k8s/10-agw-params.yaml` (which adds the `parametersRef` and rolls the proxy) silently kills an existing forward. Restart it. `curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/oauth-issuer/.well-known/oauth-authorization-server` returning `000` confirms it.

- **Transport:** `Streamable HTTP`
- **URL:** `http://localhost:8080/mcp/github`
- Click **Connect**.

In order:

1. **Entra login** — the downstream leg.
2. **The gateway consent screen** — your platform name, GitHub's logo and legal text from `19-mcp-github-elicit.yaml`.
3. **GitHub's own authorization page** — "Authorize <your app>", listing the app's permissions and the accounts it is installed on.

After that the GitHub tools list and call. Reconnect and the consent screen does not reappear — the grant is cached against your Entra identity until the GitHub refresh token expires (6 months, renewed on each refresh). `force_refresh: true` in the Helm values prompts every time.

---

## Verification

```bash
# The client's entry point -- must list the gateway itself as the auth server
curl -s http://localhost:8080/.well-known/oauth-protected-resource/mcp/github | jq

# Elicitation policy accepted
kubectl get enterpriseagentgatewaypolicy mcp-github-elicit -n agentgateway-system \
  -o jsonpath='{range .status.ancestors[0].conditions[*]}{.type}={.status} reason={.reason} msg={.message}{"\n"}{end}'

# Backend and route accepted
kubectl get agentgatewaybackend mcp-github -n agentgateway-system -o yaml
kubectl describe httproute mcp-github -n agentgateway-system
```

Drive `/authorize` directly before opening a browser. A `302` to `login.microsoftonline.com` means the issuer config and the GitHub-side config both parsed; a `500` means the upstream leg is misconfigured and the reason is in the controller log:

```bash
CID=${ENTRA_CLIENT_ID}
curl -s -D - -o /dev/null "http://localhost:8080/oauth-issuer/authorize\
?response_type=code&client_id=${CID}\
&redirect_uri=http%3A%2F%2Flocalhost%3A9999%2Fcallback&state=probe\
&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM&code_challenge_method=S256\
&resource=http%3A%2F%2Flocalhost%3A8080%2Fmcp%2Fgithub" | grep -iE '^(HTTP|location)'

kubectl logs -n agentgateway-system deploy/enterprise-agentgateway --tail=50 | grep 'auth flow'
```

Confirm the upstream endpoint independently — an unauthenticated call must return `401` with resource metadata, which is what the client's discovery depends on:

```bash
curl -s -i -X POST https://api.githubcopilot.com/mcp/ \
  -H 'Content-Type: application/json' -d '{}' | grep -i www-authenticate
```

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| GitHub shows "The redirect_uri MUST match the registered callback URL" | The app's Callback URL is not the gateway's upstream callback. Usually a port that disagrees with `gateway_config.base_url` in the Helm values. Read the `redirect_uri` out of the browser URL bar and register that literal value. |
| `500 {"error":"server_error"}` from `/oauth-issuer/authorize` | The upstream leg failed to start. Check the controller log — usually a missing Secret, the wrong client id (App ID instead of Client ID), or a `baseUrl`/issuer mismatch if you added `baseUrl`. |
| GitHub consent succeeds, then the flow dies | Look for a token-response parsing failure in the controller log. GitHub's `/login/oauth/access_token` returns form-encoded data unless the caller sends `Accept: application/json`; if that is the failure, it is a gateway-side bug to report rather than something to fix in YAML. |
| Tools list, but every call returns empty results | The GitHub App is not installed on the org or repos you are querying. Install it and pick the repositories. |
| `403` on specific tools | The app lacks that permission, or an org policy blocks it. Org owners may need to approve the app under **Third-party access**. |
| User has to re-authorize every 8 hours | The 8h token is expiring without being refreshed. Check the controller log around the refresh window; a GitHub App issues the refresh token from its own token-expiry setting, so re-check that it is enabled rather than reaching for `scopes`. |
| User has to re-authorize every hour | Almost certainly an OAuth App rather than a GitHub App: no `expires_in` means the controller assumes a 1h lifetime and has no refresh token to use. |
| `401` at the gateway with a real Entra token | Token `aud`/`iss` mismatch: v1.0 tokens are `https://sts.windows.net/<tenant>/`. See the audience and issuer notes in the README. |

## Cleanup

```bash
kubectl delete -f k8s/20-mcp-github-httproute.yaml
kubectl delete enterpriseagentgatewaypolicy mcp-github-elicit -n agentgateway-system
kubectl delete agentgatewaybackend mcp-github -n agentgateway-system
kubectl delete secret github-oauth-client -n agentgateway-system
```

Revoking access on the GitHub side is separate: uninstall the app, or revoke the user token under **Settings → Applications → Authorized GitHub Apps**.

---

## References

- [MCP consent screen](https://docs.solo.io/agentgateway/latest/mcp/token-exchange/elicitations/consent-screen/)
- [MCP elicitations and token exchange](https://docs.solo.io/agentgateway/latest/mcp/token-exchange/elicitations/)
- [GitHub MCP server](https://docs.github.com/en/copilot/how-tos/provide-context/use-mcp-in-your-ide/set-up-the-github-mcp-server)
- [github/github-mcp-server — remote server, toolsets, and endpoints](https://github.com/github/github-mcp-server)
- [Registering a GitHub App](https://docs.github.com/apps/creating-github-apps/registering-a-github-app/registering-a-github-app)
- [Refreshing user access tokens](https://docs.github.com/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens)
- [Dynamic client registration not supported (github/github-mcp-server#1404)](https://github.com/github/github-mcp-server/issues/1404)
