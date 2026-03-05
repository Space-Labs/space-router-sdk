# Space Router API Documentation

Space Router is a residential IP proxy service with three components:

| Component | Default Ports | Description |
|---|---|---|
| **Coordination API** | `8000` | Central control plane — API key management, node registry, auth validation, route selection |
| **Proxy Gateway** | `8080` (proxy), `8081` (management) | Forward proxy server that authenticates requests and routes them through home nodes |
| **Home Node** | `9090` | TLS-enabled TCP proxy running on residential IPs |

```
Agent ──► Proxy Gateway (:8080)
              │
              ├──► Coordination API (:8000)  [auth + route selection]
              │
              └──► Home Node (:9090)  [TLS proxy to target]
                       │
                       └──► Target website
```

---

## Coordination API

Base URL: `http://localhost:8000`

### Health

#### `GET /healthz`

Health check.

**Response** `200`
```json
{ "status": "ok" }
```

#### `GET /readyz`

Readiness check.

**Response** `200`
```json
{ "status": "ok" }
```

---

### API Keys

Manage API keys used by agents to authenticate proxy requests.

#### `POST /api-keys`

Create a new API key.

**Rate limit:** One key per IP address per day. Returns `429` if exceeded.

**Request body**
```json
{
  "name": "string",
  "rate_limit_rpm": 60
}
```

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | string | yes | — | Human-readable name for the key |
| `rate_limit_rpm` | integer | no | `60` | Requests per minute limit |

**Response** `201`
```json
{
  "id": "uuid",
  "name": "My Agent Key",
  "api_key": "sr_live_a1b2c3...",
  "rate_limit_rpm": 60
}
```

> The `api_key` field is only returned at creation time. Store it securely.

**Errors**

| Status | Condition |
|---|---|
| `429` | IP already created a key today |
| `500` | Internal error |

---

#### `GET /api-keys`

List all API keys. The raw key value is never returned.

**Response** `200`
```json
[
  {
    "id": "uuid",
    "name": "My Agent Key",
    "key_prefix": "sr_live_a1b2",
    "rate_limit_rpm": 60,
    "is_active": true,
    "created_at": "2025-01-15T10:30:00"
  }
]
```

---

#### `DELETE /api-keys/{key_id}`

Revoke an API key (soft-delete — sets `is_active` to false).

**Path parameters**

| Parameter | Type | Description |
|---|---|---|
| `key_id` | string (UUID) | ID of the key to revoke |

**Response** `204` No Content

---

### Nodes

Manage home node registrations.

#### `POST /nodes`

Register a new home node.

**Request body**
```json
{
  "endpoint_url": "https://1.2.3.4:9090",
  "node_type": "residential",
  "region": "us-east",
  "label": "home-office",
  "public_ip": "1.2.3.4",
  "connectivity_type": "direct"
}
```

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `endpoint_url` | string | yes | — | HTTPS URL where the node listens |
| `node_type` | string | no | `residential` | `residential` or `external_provider` |
| `region` | string | no | `null` | Geographic region label |
| `label` | string | no | `null` | Human-readable label |
| `public_ip` | string | no | `null` | Node's public IP address |
| `connectivity_type` | string | no | `direct` | `direct`, `tailscale`, or `external_provider` |

**Response** `201`
```json
{
  "id": "uuid",
  "endpoint_url": "https://1.2.3.4:9090",
  "public_ip": "1.2.3.4",
  "connectivity_type": "direct",
  "node_type": "residential",
  "status": "online",
  "health_score": 1.0,
  "region": "us-east",
  "label": "home-office",
  "created_at": "2025-01-15T10:30:00"
}
```

---

#### `GET /nodes`

List all registered nodes.

**Response** `200` — Array of node objects (same schema as `POST /nodes` response).

---

#### `PATCH /nodes/{node_id}/status`

Update a node's status.

**Path parameters**

| Parameter | Type | Description |
|---|---|---|
| `node_id` | string (UUID) | ID of the node |

**Request body**
```json
{
  "status": "online"
}
```

| Field | Type | Required | Values |
|---|---|---|---|
| `status` | string | yes | `online`, `offline`, `draining` |

**Response** `200`
```json
{ "ok": true }
```

---

#### `DELETE /nodes/{node_id}`

Remove a node from the registry.

**Response** `204` No Content

---

### Internal Endpoints

Used by the Proxy Gateway to authenticate requests and select routes. Protected by the `X-Internal-API-Key` header.

**Authentication:** All internal endpoints require the `X-Internal-API-Key` header with a value matching the `SR_INTERNAL_API_SECRET` environment variable. Returns `403` if invalid.

#### `POST /internal/auth/validate`

Validate an agent's API key.

**Headers**
```
X-Internal-API-Key: <internal_secret>
```

**Request body**
```json
{
  "key_hash": "sha256_hex_digest_of_api_key"
}
```

**Response** `200`
```json
{
  "valid": true,
  "api_key_id": "uuid",
  "rate_limit_rpm": 60
}
```

If the key is invalid or revoked:
```json
{
  "valid": false,
  "api_key_id": null,
  "rate_limit_rpm": null
}
```

---

#### `GET /internal/route/select`

Select the best available node for routing a request.

**Headers**
```
X-Internal-API-Key: <internal_secret>
```

**Response** `200`
```json
{
  "node_id": "uuid",
  "endpoint_url": "https://1.2.3.4:9090"
}
```

**Errors**

| Status | Condition |
|---|---|
| `403` | Invalid internal API key |
| `503` | No healthy nodes available |

---

#### `POST /internal/route/report`

Report the outcome of a proxied request (used for health score calculation).

**Headers**
```
X-Internal-API-Key: <internal_secret>
```

**Request body**
```json
{
  "node_id": "uuid",
  "success": true,
  "latency_ms": 250,
  "bytes": 4096
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `node_id` | string | yes | ID of the node that handled the request |
| `success` | boolean | yes | Whether the request succeeded |
| `latency_ms` | integer | yes | Round-trip latency in milliseconds |
| `bytes` | integer | yes | Total bytes transferred |

**Response** `200`
```json
{ "ok": true }
```

---

## Proxy Gateway

The gateway runs two servers:
- **Proxy server** (port `8080`) — HTTP/HTTPS forward proxy
- **Management server** (port `8081`) — Health and metrics endpoints

### Using the Proxy

Agents connect using standard HTTP proxy authentication:

```bash
# HTTP request
curl -x "http://<api_key>:@localhost:8080" http://httpbin.org/ip

# HTTPS request (CONNECT tunnel)
curl -x "http://<api_key>:@localhost:8080" --proxy-insecure https://httpbin.org/ip
```

The API key is sent as the username in `Proxy-Authorization: Basic <base64(api_key:)>`.

**Custom response headers** added by the gateway:

| Header | Description |
|---|---|
| `X-SpaceRouter-Node` | ID of the home node that handled the request |
| `X-SpaceRouter-Request-Id` | Unique request ID for tracing |

### Management Endpoints

Base URL: `http://localhost:8081`

#### `GET /healthz`

**Response** `200`
```json
{ "status": "healthy" }
```

#### `GET /readyz`

**Response** `200`
```json
{ "status": "ready" }
```

Returns `{ "status": "not_ready", "reason": "..." }` if the Coordination API is unreachable.

#### `GET /metrics`

**Response** `200`
```json
{
  "total_requests": 42,
  "active_connections": 3,
  "successful_requests": 40,
  "auth_failures": 1,
  "rate_limited": 0,
  "upstream_errors": 1,
  "no_nodes": 0
}
```

---

## Home Node

Home nodes do not expose HTTP endpoints. They listen on a TLS-encrypted TCP socket (default port `9090`) for connections from the Proxy Gateway.

**Lifecycle:**
- On startup: registers with the Coordination API via `POST /nodes`
- On shutdown: sets status to `offline` via `PATCH /nodes/{node_id}/status`

---

## Configuration Reference

All settings use the `SR_` environment variable prefix.

### Coordination API

| Variable | Type | Default | Description |
|---|---|---|---|
| `SR_PORT` | int | `8000` | Server port |
| `SR_LOG_LEVEL` | string | `INFO` | Log level |
| `SR_USE_SQLITE` | bool | `true` | Use SQLite (dev) vs Supabase (prod) |
| `SR_SQLITE_DB_PATH` | string | `space_router.db` | SQLite database file path |
| `SR_SUPABASE_URL` | string | — | Supabase project URL (production) |
| `SR_SUPABASE_SERVICE_KEY` | string | — | Supabase service role key (production) |
| `SR_INTERNAL_API_SECRET` | string | — | Shared secret for internal endpoint auth |
| `SR_IPINFO_TOKEN` | string | — | IPinfo.io API token for IP geolocation |

### Proxy Gateway

| Variable | Type | Default | Description |
|---|---|---|---|
| `SR_PROXY_PORT` | int | `8080` | Forward proxy port |
| `SR_MANAGEMENT_PORT` | int | `8081` | Management API port |
| `SR_COORDINATION_API_URL` | string | **required** | Coordination API base URL |
| `SR_COORDINATION_API_SECRET` | string | **required** | Secret for internal API auth |
| `SR_DEFAULT_RATE_LIMIT_RPM` | int | `60` | Default rate limit if not set per-key |
| `SR_NODE_REQUEST_TIMEOUT` | float | `30.0` | Timeout for requests to home nodes (seconds) |
| `SR_AUTH_CACHE_TTL` | int | `300` | Auth validation cache TTL (seconds) |
| `SR_LOG_LEVEL` | string | `INFO` | Log level |

### Home Node

| Variable | Type | Default | Description |
|---|---|---|---|
| `SR_NODE_PORT` | int | `9090` | TLS server port |
| `SR_COORDINATION_API_URL` | string | `http://localhost:8000` | Coordination API base URL |
| `SR_NODE_LABEL` | string | — | Human-readable node label |
| `SR_NODE_REGION` | string | — | Geographic region |
| `SR_NODE_TYPE` | string | `residential` | Node type |
| `SR_PUBLIC_IP` | string | — | Override auto-detected public IP |
| `SR_UPNP_ENABLED` | bool | `true` | Enable UPnP port forwarding |
| `SR_UPNP_LEASE_DURATION` | int | `3600` | UPnP lease duration (seconds) |
| `SR_BUFFER_SIZE` | int | `65536` | TCP relay buffer size (bytes) |
| `SR_REQUEST_TIMEOUT` | float | `30.0` | Per-request timeout (seconds) |
| `SR_RELAY_TIMEOUT` | float | `300.0` | TCP relay idle timeout (seconds) |
| `SR_LOG_LEVEL` | string | `INFO` | Log level |
| `SR_TLS_CERT_PATH` | string | `certs/node.crt` | TLS certificate path |
| `SR_TLS_KEY_PATH` | string | `certs/node.key` | TLS private key path |
