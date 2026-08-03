# Virtual MCP Demo

This demo shows how to federate multiple independent [Model Context Protocol (MCP)](https://modelcontextprotocol.io) servers behind a single endpoint using **Virtual MCP** on [Solo Enterprise for agentgateway](https://docs.solo.io/agentgateway/latest/mcp/virtual/).

Instead of every AI client wiring up a separate connection, URL, and credential for each MCP server, Virtual MCP multiplexes them all through one gateway endpoint. A client connects once to `/mcp` and sees the union of every tool from every backing server. Zero client-side changes are needed as servers come and go. Adding a new MCP server to the federation is as simple as adding a label to a Kubernetes Service.

---

## Architecture

```
                         ┌───────────────────────────────────────────────┐
                         │        agentgateway  (Virtual MCP)            │
   MCP client            │                                               │
   (Inspector,   ──────► │   HTTPRoute  /mcp                             │
    Kiro,    streamable  │        │  ▲ jwtAuthentication (Entra ID)      │
    agent)      HTTP     │        ▼                                      │
                         │   EnterpriseAgentgatewayBackend "mcp"         │
                         │        │  ▲ mcp.authorization (tool filter)   │
                         │        │                                      │
                         │        ├─ selector  type=mcp-server           │──► mcp-server-everything  (streamable HTTP)
                         │        │                                      │      └─ echo, get-sum, get-env, ...
                         │        ├─ static    mcp-website-fetcher       │──► mcp-website-fetcher     (SSE)
                         │        │                                      │      └─ fetch
                         │        └─ selector  type=mcp-server           │┈┈► my-mcp-server          (streamable HTTP)
                         │                                               │      └─ read_file, write_file, list_directory, ...
                         └───────────────────────────────────────────────┘

   One connection  ──►  one endpoint  ──►  tools from every backing MCP server
   (selector-discovered tools are prefixed `<service>-<port>_`, e.g.
    `mcp-server-everything-3001_echo`; static targets use the declared name,
    e.g. `mcp-website-fetcher_fetch`)
```

The single `EnterpriseAgentgatewayBackend` federates several independent servers using one config:

| Target                     | Transport        | Federation style | Distinct tools                          | Notes                                                                 |
| -------------------------- | ---------------- | ---------------- | --------------------------------------- | --------------------------------------------------------------------- |
| `mcp-server-everything`    | Streamable HTTP  | **Label selector** | `echo`, `add`, `printEnv`, …           | Selectors auto-discover any Service with the matching label.          |
| `mcp-website-fetcher`      | SSE              | **Static target**  | `fetch`                                | Label selectors only support streamable HTTP today; SSE must be static. |
| `my-mcp-server`           | Streamable HTTP  | **Label selector** | `read_file`, `write_file`, `list_directory`, … | Deployed in the Scale step to show a *different* toolset joining live. |

> **Note:** Only streamable HTTP is currently supported for label selectors. If a server speaks SSE, declare it as a `static` target.

---

## What You'll Demo

1. **Federation** — two separate MCP servers appear as one tool catalog through a single `/mcp` endpoint.
2. **Mixed transports** — streamable HTTP (via selector) and SSE (via static target) coexist in one backend.
3. **Scale by label** — deploy a second streamable-HTTP server with the right label and it joins the federation automatically, with no change to the gateway, route, or backend.
4. **Resilience** — `failureMode: FailOpen` keeps the surviving tools available even if one target is down.
5. **Per-identity tool filtering** — an `EnterpriseAgentgatewayPolicy` gives each caller a different tool catalog from the same `/mcp` URL, keyed on Microsoft Entra ID app roles and enforced at the gateway.

---

## Prerequisites

- A Kubernetes cluster (e.g. [Kind](https://kind.sigs.k8s.io/), k3d, or any managed cluster) with `kubectl` context set
- [`helm`](https://helm.sh/docs/intro/install/) v3.x
- A **Solo Enterprise for agentgateway** license key: [request a trial](https://www.solo.io/products/agentgateway)
- [Node.js](https://nodejs.org/) 20+ (for the MCP Inspector client, run via `npx`)
- [agentregistry cli (arctl)](https://aregistry.ai/docs/quickstart/#setup)
- [Docker Engine (Docker Desktop or similar)](https://docs.docker.com/desktop/)

For the [tool filtering](#filter-tools-by-identity-microsoft-entra-id) section only:

- `envsubst` (`brew install gettext`) to substitute IDs into the Entra manifests
- [`az` CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) to mint test tokens.
- A Microsoft Entra ID tenant with permission to create an app registration

Set your license key:

```bash
export AGENTGATEWAY_LICENSE_KEY=<your-license-key>
```

---

## Quick Start

Run the steps in order from the repo root. Every manifest lives in [`k8s/`](k8s/).

### 1. Install the Gateway API CRDs

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.5.0/standard-install.yaml
```

### 2. Install Solo Enterprise for agentgateway

```bash
# CRDs
helm upgrade -i enterprise-agentgateway-crds \
  oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway-crds \
  --create-namespace \
  --namespace agentgateway-system \
  --version v2026.7.0

# Control plane
helm upgrade -i enterprise-agentgateway \
  oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
  -n agentgateway-system \
  --version v2026.7.0 \
  --set-string licensing.licenseKey=${AGENTGATEWAY_LICENSE_KEY}

kubectl rollout status deploy -n agentgateway-system --timeout=120s
```

### 3. Create the gateway proxy

```bash
kubectl apply -f k8s/02-gateway.yaml
kubectl get gateway agentgateway-proxy -n agentgateway-system
```

### 4. Deploy the two MCP servers

```bash
kubectl apply -f k8s/00-mcp-server-everything.yaml
kubectl apply -f k8s/01-mcp-website-fetcher.yaml
kubectl rollout status deploy/mcp-server-everything --timeout=120s
kubectl rollout status deploy/mcp-website-fetcher --timeout=120s
```

### 5. Federate them with Virtual MCP

```bash
kubectl apply -f k8s/03-virtual-mcp-backend.yaml
kubectl apply -f k8s/04-httproute.yaml
kubectl describe httproute mcp
```

### 6. Connect and verify

Port-forward the gateway:

```bash
kubectl port-forward deployment/agentgateway-proxy -n agentgateway-system 8080:80
```

In a second terminal, launch the [MCP Inspector](https://github.com/modelcontextprotocol/inspector):

```bash
npx @modelcontextprotocol/inspector@0.21.2
```

In the Inspector UI:

- **Transport:** `Streamable HTTP`
- **URL:** `http://localhost:8080/mcp`
- Click **Connect**, then open the **Tools** tab and **List Tools**.

You should see tools from _both_ servers in one list the `mcp-server-everything` tools (`echo`, `add`, `printEnv`, `longRunningOperation`, …) alongside the `fetch` tool from `mcp-website-fetcher`.

---

## Scale the Federation

Start a local agentregistry and build/publish the example MCP server in `mcp/my-mcp-server` to it. Then, load the image to your `kind` node (if using `kind`):

```bash
arctl daemon start
arctl mcp build mcp/my-mcp-server --image my-mcp-server
arctl mcp publish user/my-mcp-server --type oci --package-id my-mcp-server --description "mcp server" --version 0.1.0
kind load docker-image my-mcp-server:latest
```

```bash
kubectl apply -f k8s/05-my-mcp-server.yaml
kubectl rollout status deploy/my-mcp-server --timeout=120s
```

First click `Reconnect`, then re-run `List Tools` in the Inspector. The `my-mcp-server-*` tools now appear alongside the others, no config edit required.

---

## Filter Tools by Identity (Microsoft Entra ID)

You can attach an MCP `EnterpriseAgentgatewayPolicy` policy to the Virtual MCP backend to filter the tool catalog per-client, computed from the claims in their token.

Filtering is enforced on both sides of the protocol:

| Method       | Behavior for a disallowed tool                                            |
| ------------ | ------------------------------------------------------------------------- |
| `tools/list` | Tools that the client does not have access to see are filtered from the returned list.         |
| `tools/call` | Calls to a disallowed tool fail with `-32602 Unknown tool`. The existence of the tool is not leaked by the error.   |

Rules are CEL expressions in `spec.backend.mcp.authorization.policy.matchExpressions`. A tool is served if at least one expression matches. Any non-matched tools are not served (no implicit allow).

### Available CEL variables

| Variable            | Contains                                                                 |
| ------------------- | ------------------------------------------------------------------------ |
| `mcp.tool.name`     | The tool name.                               |
| `mcp.tool.target`   | The federated target the tool came from.         |
| `mcp.prompt.name`   | Prompt name, for filtering `prompts/*`.                                  |
| `mcp.resource.name` | Resource name, for filtering `resources/*`.                              |
| `jwt.<claim>`       | Any claim from the validated token (eg `jwt.sub`, `jwt.roles`, `jwt.groups`). Guard with `has()`. |

> **Note that `mcp.tool.name` is the base tool name, not the client-visible one returned from the gateway (eg `echo`, not `mcp-server-everything-3001_echo`)**

> **Note that Entra outputs jwt.groups as GUIDs, not names**

> **Note that `mcp.tool.target` differs based on target type**
> - For a `static` target it is the `name` from the backend spec (`mcp-website-fetcher`).
> - For a `selector` target, agentgateway discovers one target per matching Service and names it `<service>-<port>`.
>   - The target declared as `mcp-servers` in [`k8s/03-virtual-mcp-backend.yaml`](k8s/03-virtual-mcp-backend.yaml) for example would be: `mcp-server-everything-3001`
> - Matching `mcp.tool.target == "mcp-servers"` matches nothing and silently filters out every tool. Read the real names off the tool prefixes in a `tools/list` response before writing selector-based rules.

### Step 1 — Create an anonymous filter

```bash
kubectl apply -f k8s/06-tool-filter-anonymous.yaml
```

Within Inspector, run `List Tools` (click `Reconnect` first), or from the CLI:

```bash
npx @modelcontextprotocol/inspector@0.21.2 http://localhost:8080/mcp --method tools/list --cli
```

The catalog drops from 14 tools to 3: two allowed by name plus the whole `mcp-website-fetcher` target:

```
mcp-server-everything-3001_echo
mcp-server-everything-3001_get-sum
mcp-website-fetcher_fetch
```

`get-sum` still works, but `get-env` is refused:

```bash
# allowed
npx @modelcontextprotocol/inspector@0.21.2 http://localhost:8080/mcp \
  --method tools/call --tool-name mcp-server-everything-3001_get-sum \
  --tool-arg a=2 --tool-arg b=3 --cli

# {"code":-32602,"message":"Unknown tool: ..."}
npx @modelcontextprotocol/inspector@0.21.2 http://localhost:8080/mcp \
  --method tools/call --tool-name mcp-server-everything-3001_get-env --cli
```

### Step 2 — Register the API in Microsoft Entra ID

To key the rules on identity, the gateway needs tokens to validate. In the [Azure portal](https://portal.azure.com):

1. `App registrations → New registration.` Name it (e.g. `virtual-mcp`). Leave redirect URIs empty.
2. Copy the `Application (client) ID` and `Directory (tenant) ID`.
3. `Expose an API → Add a scope.` Accept the default App ID URI (`api://<client-id>`), then add a scope named `mcp_access`. Set `Who can consent` to *Admins and users*.
4. `App roles → Create app role` for each tier, allowed member types `Users/Groups`:

   | Display name | Value         | Grants                              |
   | ------------ | ------------- | ----------------------------------- |
   | Tools Read   | `Tools.Read`  | `echo`, `get-sum`, and every `get-*` tool |
   | Web Read     | `Web.Read`    | the whole `mcp-website-fetcher` target |
   | Tools Admin  | `Tools.Admin` | the entire federated catalog         |

5. `Enterprise applications → your app → Users and groups`: assign users or groups to those roles. Start with `Tools.Read` only, so the filtering is visibly partial rather than all-or-nothing.
  - You can change the role attached to your user here to test the different policy filters in Step 4.
6. `Expose an API → Add a client application`: pre-authorize the clients allowed to request `mcp_access` without a consent prompt. 
  - Note that the Azure CLI must be added here for the `az` command in Step 4 to work: `04b07795-8ddb-461a-bbee-02f9e1bf7b46`

### Step 3 — Wire Entra into the gateway

```bash
export ENTRA_TENANT_ID=<directory (tenant) ID>
export ENTRA_CLIENT_ID=<application (client) ID>
```

The gateway has to fetch Entra's signing keys, so add Microsoft's login endpoint as a static `AgentgatewayBackend`:

```bash
kubectl apply -f k8s/07-entra-jwks-backend.yaml
```

Then attach JWT authentication to the `HTTPRoute`:

```bash
envsubst < k8s/08-mcp-entra-authn.yaml | kubectl apply -f -
```

The `mcp` block in that policy also publishes an OAuth 2.0 Protected Resource Metadata document (RFC 9728), so MCP clients discover Entra and run the login flow on their own.

> **NOTE**: Entra issues **v1.0** access tokens by default, whose `iss` is `https://sts.windows.net/<tenant>/` (with a trailing slash included). If you set `"accessTokenAcceptedVersion": 2` in the app manifest, change the issuer to `https://login.microsoftonline.com/<tenant>/v2.0`. The v2.0 keys endpoint serves signing keys for both versions, so `jwksPath` is the same either way.

### Step 4 — Swap in role-based tool filtering

```bash
kubectl apply -f k8s/09-mcp-tool-authz.yaml
```

This reuses the policy name from Step 1, so it replaces the anonymous rules with ones that read `jwt.roles`:

```yaml
matchExpressions:
  - 'has(jwt.roles) && "Tools.Read" in jwt.roles && mcp.tool.name in ["echo", "get-sum"]'
  - 'has(jwt.roles) && "Web.Read" in jwt.roles && mcp.tool.target == "mcp-website-fetcher"'
  - 'has(jwt.roles) && "Tools.Read" in jwt.roles && mcp.tool.name.startsWith("get-")'
  - 'has(jwt.roles) && "Tools.Admin" in jwt.roles'
```

Confirm the policy attached and its CEL compiled. The `reason` field must be `Valid`:

```bash
kubectl get enterpriseagentgatewaypolicy mcp-tool-filter \
  -o jsonpath='{range .status.ancestors[0].conditions[*]}{.type}={.status} reason={.reason} msg={.message}{"\n"}{end}'
```

With Step 3 applied, an unauthenticated caller is rejected before filtering:

```bash
npx @modelcontextprotocol/inspector@0.21.2 http://localhost:8080/mcp --method tools/list --cli
# {"error":"unauthorized","error_description":"JWT token required"}
```

Next, issue a token with the `az` CLI and include it in the `tools/list` call:

```bash
export TOKEN=$(az account get-access-token --resource api://${ENTRA_CLIENT_ID} --query accessToken -o tsv)

npx @modelcontextprotocol/inspector@0.21.2 http://localhost:8080/mcp \
  --header "Authorization: Bearer ${TOKEN}" --method tools/list --cli
```

You should see the list of filtered tools allowed by the `Tools.Read` role:

```bash
mcp-server-everything-3001_echo
mcp-server-everything-3001_get-annotated-message
mcp-server-everything-3001_get-env
mcp-server-everything-3001_get-resource-links
mcp-server-everything-3001_get-resource-reference
mcp-server-everything-3001_get-structured-content
mcp-server-everything-3001_get-sum
mcp-server-everything-3001_get-tiny-image
```

---

## Kiro

To connect to the unified MCP endpoint in the Kiro IDE, add a `.kiro/settings/mcp.json` to your workspace:

```bash
mkdir -p .kiro/settings
cat <<EOF >> .kiro/settings/mcp.json
{
  "mcpServers": {
    "ARCTL": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
EOF
```

Then open in Kiro:

```bash
kiro .
```

You can now see all avialable tools in the Kiro panel -> MCP servers list:

![Kiro tools list](./img/kiro-tools.png)

---

## Tracing

Install Tempo and Grafana:

```bash
helm repo add grafana-community https://grafana-community.github.io/helm-charts
helm upgrade --install tempo grafana-community/tempo --namespace=telemetry
helm upgrade --install grafana grafana-community/grafana --namespace=telemetry
```

Install OTEL Collector:

```bash
helm upgrade --install opentelemetry-collector-traces opentelemetry-collector \
--repo https://open-telemetry.github.io/opentelemetry-helm-charts \
--version 0.127.2 \
--set mode=deployment \
--set image.repository="otel/opentelemetry-collector-contrib" \
--set command.name="otelcol-contrib" \
--namespace=telemetry \
--create-namespace \
-f -<<EOF
config:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317
        http:
          endpoint: 0.0.0.0:4318
  exporters:
    otlp/tempo:
      endpoint: http://tempo.telemetry.svc.cluster.local:4317
      tls:
        insecure: true
    debug:
      verbosity: detailed
  service:
    pipelines:
      traces:
        receivers: [otlp]
        processors: [batch]
        exporters: [debug, otlp/tempo]
EOF
```

Verify all pods are up and running in the `telemetry` namespace:

```bash
kubectl get pods -n telemetry
```

Create an `EnterpriseAgentgatewayPolicy` to configure tracing:

```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: tracing
  namespace: agentgateway-system
spec:
  targetRefs:
    - kind: Gateway
      name: agentgateway-proxy
      group: gateway.networking.k8s.io
  frontend:
    tracing:
      backendRef:
        name: opentelemetry-collector-traces
        namespace: telemetry
        port: 4317
      protocol: GRPC
      clientSampling: "true"
      randomSampling: "true"
      resources:
        - name: deployment.environment.name
          expression: '"production"'
        - name: service.version
          expression: '"test"'
      attributes:
        add:
          - expression: 'request.headers["x-header-tag"]'
            name: request
          - expression: 'request.host'
            name: host
EOF
```

Next, send 10 `tools/list` calls to generate trace data:

```bash
for i in {1..10}; do npx @modelcontextprotocol/inspector@0.21.2 http://localhost:8080/mcp  --method tools/list --cli >> /dev/null; done
```

Next, get the admin password for Grafana by retrieving the `admin-password` value from the `grafana` secret in the `telemetry` namespace:

```bash
kubectl get secret -n telemetry grafana -o jsonpath='{ .data.admin-password }' | base64 -d && echo
```

Then, port-forward Grafana and access the admin UI using the password retrieved above:

```bash
kubectl port-forward svc/grafana -n telemetry 3000:80
```

```bash
open http://localhost:3000
```

Now you can add Tempo as a data source and query for traces. Add Tempo by clicking on `Connections -> Data Sources` then click to add a Tempo datasource.

Under **URL**, add this: `http://tempo.telemetry.svc.cluster.local:3200`

![Tempo data source](./img/tempo-datasource.png)

Finally, you can now query for trace data using the `Explore` tab. Navigate to [http://localhost:3000/explore](http://localhost:3000/explore)

Using TraceQL, you can query for traces you generated with: `{ .service.name = "agentgateway-proxy"}`

![Tempo explore](./img/tempo-explore.png)

---

## Configuration

Everything is driven by [`k8s/03-virtual-mcp-backend.yaml`](k8s/03-virtual-mcp-backend.yaml).

| Field                         | Purpose                                                                                     |
| ----------------------------- | ------------------------------------------------------------------------------------------- |
| `spec.mcp.targets[].selector` | Dynamically federate every Service matching the labels (streamable HTTP only).              |
| `spec.mcp.targets[].static`   | Federate a fixed `host`/`port`/`protocol`. Use this for SSE servers.                       |
| `spec.mcp.targets[].name`     | Prefix applied to that target's tools in the federated listing (e.g. `mcp-website-fetcher_fetch`). |
| `spec.mcp.failureMode`        | `FailOpen` (serve surviving targets if one is down) or `FailClosed` (fail the listing).     |

Tool filtering and authentication are driven by `EnterpriseAgentgatewayPolicy`:

| Field                                                | Purpose                                                                                  |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `spec.backend.mcp.authorization.policy.matchExpressions` | CEL rules, OR'd, selecting which tools/prompts/resources a caller may see and call.   |
| `spec.backend.mcp.authorization.action`               | `Allow` (default), `Deny`, or `Require`.                                                 |
| `spec.traffic.jwtAuthentication.providers[]`          | Token issuer, audiences, and JWKS source. Attach to the `HTTPRoute`.  |
| `spec.traffic.jwtAuthentication.mcp.resourceMetadata` | Publishes RFC 9728 Protected Resource Metadata so MCP clients can discover the IdP.      |

To target a different release, change `--version v2026.7.0` in the Helm commands to match your licensed version.

---

## Testing / Verification

Quick checks without the Inspector UI:

```bash
# The backend and route are accepted
kubectl get enterpriseagentgatewaybackend mcp -o yaml
kubectl describe httproute mcp

# All MCP servers are Ready
kubectl get pods -l app=mcp-server-everything
kubectl get pods -l app=mcp-website-fetcher

# Gateway is programmed
kubectl get gateway agentgateway-proxy -n agentgateway-system \
  -o jsonpath='{.status.conditions[?(@.type=="Programmed")].status}{"\n"}'
```

Test `FailOpen`: scale one target to zero, then re-list tools in the Inspector.

```bash
kubectl scale deploy/mcp-website-fetcher --replicas=0
# re-list tools; mcp-server-everything tools remain available
kubectl scale deploy/mcp-website-fetcher --replicas=1
```

Check tool-filtering policies:

```bash
# Policy attached and CEL compiled? reason must be Valid -- PartiallyValid means
# at least one matchExpression failed to compile and is being ignored.
kubectl get enterpriseagentgatewaypolicy mcp-tool-filter \
  -o jsonpath='{range .status.ancestors[0].conditions[*]}{.type}={.status} reason={.reason} msg={.message}{"\n"}{end}'

# Discover the real mcp.tool.target names from the tool prefixes
npx @modelcontextprotocol/inspector@0.21.2 http://localhost:8080/mcp --method tools/list --cli \
  | grep '"name"'
```

An unexpectedly empty tool list could be caused by:

- a rule matched `mcp.tool.target` against the *declared* target name instead of `<service>-<port>`
- a rule matched the client-visible prefixed tool name instead of the base `mcp.tool.name`
- the token is missing the expected role, or its `aud`/`iss` doesn't match the provider config

---

## Cleanup

```bash
# Tool filtering / Entra auth
kubectl delete enterpriseagentgatewaypolicy mcp-tool-filter --ignore-not-found
kubectl delete enterpriseagentgatewaypolicy mcp-entra-authn --ignore-not-found
kubectl delete -f k8s/07-entra-jwks-backend.yaml --ignore-not-found

# Demo resources
kubectl delete -f k8s/05-my-mcp-server.yaml --ignore-not-found
kubectl delete -f k8s/04-httproute.yaml --ignore-not-found
kubectl delete -f k8s/03-virtual-mcp-backend.yaml --ignore-not-found
kubectl delete -f k8s/01-mcp-website-fetcher.yaml --ignore-not-found
kubectl delete -f k8s/00-mcp-server-everything.yaml --ignore-not-found
kubectl delete -f k8s/02-gateway.yaml --ignore-not-found

# agentgateway control plane (optional — removes the whole install)
helm uninstall enterprise-agentgateway -n agentgateway-system
helm uninstall enterprise-agentgateway-crds -n agentgateway-system
kubectl delete namespace agentgateway-system
```

---

## Project Structure

```
.
├── README.md
└── k8s/
    ├── 00-mcp-server-everything.yaml     # MCP server #1 (streamable HTTP)
    ├── 01-mcp-website-fetcher.yaml       # MCP server #2 (SSE)
    ├── 02-gateway.yaml                   # agentgateway-proxy Gateway
    ├── 03-virtual-mcp-backend.yaml       # ★ Virtual MCP federation backend
    ├── 04-httproute.yaml                 # Exposes the federation at /mcp
    ├── 05-my-mcp-server.yaml            # Scale-by-label demo (distinct toolset)
    ├── 06-tool-filter-anonymous.yaml     # ★ Tool filtering, no IdP required
    ├── 07-entra-jwks-backend.yaml        # login.microsoftonline.com as a static backend
    ├── 08-mcp-entra-authn.yaml           # Entra JWT validation + RFC 9728 metadata
    └── 09-mcp-tool-authz.yaml            # ★ Tool filtering keyed on Entra app roles
```

---

## Version Requirements

| Component                          | Version    |
| ---------------------------------- | ---------- |
| Solo Enterprise for agentgateway   | `v2026.7.0` |
| Kubernetes Gateway API             | `v1.5.0`   |
| MCP Inspector                      | `0.21.2`   |
| Node.js (for Inspector)            | `20+`      |

---

## References

- [Virtual MCP documentation](https://docs.solo.io/agentgateway/latest/mcp/virtual/)
- [Install Solo Enterprise for agentgateway](https://docs.solo.io/agentgateway/latest/quickstart/install/)
- [MCP authorization (CEL rules)](https://agentgateway.dev/docs/standalone/main/mcp/mcp-authz/)
- [Set up MCP auth](https://docs.solo.io/agentgateway/latest/mcp/auth/setup/)
- [Enterprise MCP SSO with Microsoft Entra and agentgateway](https://www.solo.io/blog/enterprise-mcp-sso-with-microsoft-entra-and-agentgateway)
- [Microsoft Entra: configure app roles](https://learn.microsoft.com/entra/identity-platform/howto-add-app-roles-in-apps)
- [Model Context Protocol](https://modelcontextprotocol.io)
- [MCP filesystem server](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem)
