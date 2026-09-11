# XAA + Agent as a Principal

A working implementation of a **two-agent architecture** combining Cross-App Access (XAA) with Agent as a Principal — where an Okta AI Agent orchestrates cross-IdP identity federation, and an Auth0 observer agent becomes a named, auditable principal in every downstream API call.

Built against:
- Auth0 Cross-App Access docs: https://auth0.com/docs/ai-agents-mcp/cross-app-access
- Auth0 Agent as a Principal docs: https://auth0.com/docs/ai-agents-mcp/agents-as-principal

---

## What this project demonstrates

This project implements a **two-agent architecture**:

**Orchestrator Agent** (Okta AI Agent `A0_XAA`) — handles identity federation across IdP boundaries. It acquires the user's identity from Okta and federates it into Auth0's domain via XAA. Its role is infrastructure: cross the boundary, hand off the token. It does not appear in the final token.

**Observer Agent** (Auth0 Agent Object `agt_xxx`) — executes the actual API call as a named principal. Its identity is stamped into every token it uses as `act.sub`, making it visible and auditable to the downstream API.

The final access token carries two identities:

```json
{
  "sub":  "okta|user123",
  "act": {
    "sub": "agt_xxxxxxxxxxxxxxxxxxxx"
  }
}
```

- `sub` — the user the agent is acting for
- `act.sub` — the observer agent that made the request

The downstream API can answer two accountability questions:
- **"Which user was accessed?"** → `sub`
- **"Which agent did it?"** → `act.sub`

Observability is intentionally split: the orchestrator's actions are visible in Okta audit logs (Steps 1–2). The observer agent's identity is carried in the token chain (Step 3.5 onward).

---

## Architecture

```mermaid
sequenceDiagram
    actor User

    box AliceBlue Okta (Identity Provider)
        participant OrgAS as Org AS<br/>/oauth2/v1/token
        participant ResApp as Resource App<br/>(XAA trust anchor)
    end

    box Lavender Okta Orchestrator Agent (A0_XAA)
        participant RWA as XAA Requesting Party<br/>Regular Web App
    end

    box OldLace Auth0 (Resource Authorization Server)
        participant A0JWT as /oauth/token<br/>jwt-bearer grant
        participant A0OBO as /oauth/token<br/>OBO token exchange
        participant AgentObj as Agent Object<br/>agt_xxxxxxxxxxxxxxxxxxxx
    end

    box LightGreen Observer Agent — Auth0 (agt_xxx)
        participant M2M as M2M Custom API Client<br/>linked to agt_xxxxxxxxxxxxxxxxxxxx
    end

    participant API as Protected API :8080

    User->>RWA: 1a · login (Authorization Code + PKCE)
    RWA->>OrgAS: redirect to Okta
    OrgAS-->>RWA: 1b · id_token (PKCE callback)

    RWA->>OrgAS: 2 · token-exchange<br/>requested_token_type = id-jag<br/>audience = Auth0 issuer URL
    OrgAS-->>RWA: ID-JAG (signed by Okta, targeted at Auth0)

    RWA->>A0JWT: 3 · jwt-bearer<br/>assertion = ID-JAG<br/>connection = your-okta-connection
    A0JWT-->>RWA: access token (sub = user)

    RWA->>M2M: hand off access token
    M2M->>A0OBO: 3.5 · token-exchange OBO<br/>subject_token = access_token<br/>client_id = M2M → agt_xxx
    A0OBO-->>M2M: delegated token (sub = user · act.sub = agt_xxx)

    M2M->>API: 4 · GET /data  Bearer delegated_token
    API-->>M2M: { acting_user, acting_agent, data }
```

---

## Token flow

```
Step 1   User logs in to Okta (Authorization Code + PKCE)
             → Okta issues id_token
             → Orchestrator Agent (A0_XAA) receives the id_token via PKCE callback

Step 2   Orchestrator Agent exchanges id_token at Okta Org AS → ID-JAG
             → Okta issues ID-JAG targeted at Auth0 tenant issuer
             → Orchestrator's Okta identity (OKTA_CLIENT_ID) authenticates this call
             grant_type           = urn:ietf:params:oauth:grant-type:token-exchange
             requested_token_type = urn:ietf:params:oauth:token-type:id-jag
             audience             = https://your-tenant.auth0.com

Step 3   Orchestrator Agent presents ID-JAG at Auth0 (jwt-bearer)
             → Auth0 validates ID-JAG against Okta JWKS
             → Auth0 looks up user via your-okta-connection enterprise connection
             → Auth0 issues access token:  sub = user
             grant_type = urn:ietf:params:oauth:grant-type:jwt-bearer
             assertion  = <ID-JAG>
             connection = your-okta-connection

Step 3.5 Observer Agent (agt_xxx) performs OBO token exchange at Auth0
             → Orchestrator hands access token to Observer Agent's M2M client
             → Auth0 recognises M2M client is linked to the agent object (agt_xxx)
             → Auth0 stamps act.sub = agt_xxx into new token
             → Token now carries both user identity and observer agent identity
             grant_type         = urn:ietf:params:oauth:grant-type:token-exchange
             subject_token      = <Step 3 access token>
             client_id          = <M2M client>   ← linked to agt_xxx
             audience           = https://your-api-identifier/

Step 4   Observer Agent calls protected API with OBO delegated token
             → API validates JWT, reads sub + act.sub
             → Returns { acting_user, acting_agent, data }
```

---

## Key concepts

### Two-agent architecture

This project separates agent responsibilities into two distinct roles:

| Role | Agent | Identity used | Visible where |
|---|---|---|---|
| **Orchestrator** | Okta AI Agent (`A0_XAA`) | `OKTA_CLIENT_ID` at Okta | Okta audit logs (Steps 1–2) |
| **Observer** | Auth0 Agent Object (`agt_xxx`) | `act.sub` in token | Token claims (Step 3.5 onward) |

The **orchestrator's** sole job is identity plumbing — crossing IdP boundaries to get a user token from Okta's domain into Auth0's domain. It is not a named principal in any token. Its accountability lives in Okta's logs.

The **observer's** job is task execution — it calls the API and its identity is stamped into every token it uses. The downstream API holds it accountable via `act.sub`.

These are intentionally different agents with different roles. The split observability is the design, not a gap: Okta owns the orchestrator's audit trail, Auth0 owns the observer's.

### Cross-App Access (XAA)
XAA allows an agent to obtain a delegated access token for a downstream API using the user's identity from a different IdP — without requiring the user to log in again. Okta issues an **ID-JAG** (Identity Assertion Authorization Grant), a signed JWT that Auth0 accepts as proof of the user's identity.

The Okta **Resource App** is not a server or API — it is a trust configuration object that tells Okta: *"Auth0's issuer URL is a valid audience for ID-JAGs from this org."* It can be a Web App type because it does nothing active in the flow.

### Agent as a Principal
Without Agent as a Principal, the XAA flow produces a token where only the user's identity appears (`sub = user`). The agent is invisible — there is no way to tell which agent accessed the API.

Agent as a Principal introduces a registered **agent object** in Auth0 with a stable `agent_id`. When the agent's M2M client performs the OBO token exchange, Auth0 stamps `act.sub = agent_id` into the new token. The agent becomes a first-class identity alongside the user.

### Why this matters at scale

| Without Agent as a Principal | With Agent as a Principal |
|---|---|
| `sub = user` only | `sub = user`, `act.sub = agent_id` |
| Logs show: "user accessed API" | Logs show: "user accessed API via agent Y" |
| Cannot distinguish agents | Per-agent audit trail |
| Cannot revoke one agent | Can revoke specific agent |
| No agent accountability | Full agent governance |

---

## Objects required

**Okta:**

| Object | Type | Purpose |
|---|---|---|
| `A0_XAA` AI Agent | Okta AI Agent | Orchestrator. Authenticates Steps 1–2 using `OKTA_CLIENT_ID`. Requests ID-JAGs targeted at Auth0. |
| Auth0 Resource App | OIDC Web App (XAA enabled) | Trust anchor. Registers Auth0's issuer URL as a valid ID-JAG audience. Not active in the flow. |

**Auth0:**

| Object | Type | Purpose |
|---|---|---|
| XAA API | Resource Server | Represents the protected API. Identifier = `https://your-api-identifier/` |
| XAA Requesting Party | Regular Web App | jwt-bearer conduit (Step 3). Presents ID-JAG to Auth0. Must have Okta enterprise connection enabled. |
| XAA Requesting Party — M2M | Machine to Machine | Observer Agent's client (Step 3.5). Must be created from the API page (Custom API Client). Linked to Agent Object. |
| `agt_xxx` | Agent Object | Observer Agent identity. Stamped as `act.sub` in OBO tokens. Linked to the M2M client. |

---

## Project structure

```
A0_Agent as a Principal/
├── agent.py          # Core XAA + OBO logic (Steps 2–4). Importable module.
├── api_server.py     # Protected API: validates JWT, returns acting_user + acting_agent
├── ui.py             # Flask web UI: browser login, calls agent.py for Steps 2–4
├── templates/
│   └── index.html    # UI: shows all tokens with raw + decoded (jwt.io style) side by side
├── .env              # Your credentials (never commit)
├── .env.example      # Template with all required variables
└── requirements.txt
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure `.env`

Copy `.env.example` to `.env` and fill in:

| Variable | Where to find it |
|---|---|
| `OKTA_DOMAIN` | Okta Admin → top-right org URL |
| `OKTA_CLIENT_ID` | Okta Admin → Applications → agent app → Client ID |
| `OKTA_CLIENT_SECRET` | Okta Admin → Applications → agent app → Client Secrets |
| `AUTH0_DOMAIN` | Auth0 Dashboard → Settings → Domain |
| `AUTH0_API_AUDIENCE` | Auth0 Dashboard → Applications → APIs → your API → Identifier |
| `AUTH0_CLIENT_ID` | Auth0 Dashboard → XAA Requesting Party (Regular Web App) → Client ID |
| `AUTH0_CLIENT_SECRET` | Auth0 Dashboard → XAA Requesting Party → Client Secret |
| `AUTH0_CONNECTION` | Auth0 Dashboard → Authentication → Enterprise → Okta connection → Name |
| `AUTH0_API_SCOPE` | Scope registered on Okta Resource App (e.g. `xaa:read`) |
| `OKTA_RESOURCE_AS_ISSUER` | Okta Admin → Security → API → Authorization Servers → Issuer |
| `AUTH0_AGENT_ID` | Auth0 Dashboard → AI Agents → your agent → Agent ID |
| `AUTH0_AGENT_CLIENT_ID` | Auth0 Dashboard → XAA Requesting Party M2M → Client ID |
| `AUTH0_AGENT_CLIENT_SECRET` | Auth0 Dashboard → XAA Requesting Party M2M → Client Secret |

### 3. Auth0 one-time configuration (UI steps)

All setup is done through the dashboards — no scripts needed.

---

#### A. Okta — Create the AI Agent app

1. Okta Admin → **Applications → Applications → Create App Integration**
2. Sign-in method: **OIDC - OpenID Connect** → App type: **Web Application** → Next
3. Name: `XAA Agent App`
4. Under **Advanced**, set **Application type** to `AI Agent` (enables the `ai_agent` profile)
5. Copy **Client ID** and **Client Secret** → `OKTA_CLIENT_ID`, `OKTA_CLIENT_SECRET`

---

#### B. Okta — Create the Resource App (XAA trust anchor)

This app is a trust configuration object, not a real API. It tells Okta that Auth0's issuer URL is a valid ID-JAG consumer.

1. Okta Admin → **Applications → Applications → Create App Integration**
2. Sign-in method: **OIDC - OpenID Connect** → App type: **Web Application** → Next
3. Name: `Auth0 Resource App` (or any name)
4. Under **Cross-App Access**, enable **Allow other applications to request access**
5. Set **Issuer URI** to `https://your-tenant.auth0.com` (your Auth0 domain, no trailing slash)
6. Add a scope: `xaa:read` → Save

---

#### C. Okta — Connect the Agent App to the Resource App

1. On the **XAA Agent App** → **Resource Connections** tab
2. Click **Add Connection** → select `Auth0 Resource App`
3. Select scope: `xaa:read` → Save
4. This enables Step 2: the agent app can now request an ID-JAG targeted at Auth0

> **Important:** Both apps must use the **Org AS** (`/oauth2/v1`), not the Default Custom AS. ID-JAG token type is only supported at the Org AS.

---

#### D. Auth0 — Create the Enterprise OIDC connection (Okta → Auth0)

1. Auth0 Dashboard → **Authentication → Enterprise → OpenID Connect → + Create Connection**
2. Name your connection (this becomes `AUTH0_CONNECTION`)
3. Issuer URL: `https://your-org.okta.com` (Okta domain)
4. Client ID / Secret: from the **XAA Agent App** in Okta (or create a dedicated OIDC app in Okta for this)
5. Save — copy the connection **Name** → `AUTH0_CONNECTION`

---

#### E. Auth0 — Create the API (Resource Server)

1. Auth0 Dashboard → **Applications → APIs → + Create API**
2. Name: `XAA API`
3. Identifier: `https://your-api-identifier/` → this becomes `AUTH0_API_AUDIENCE`
4. Signing Algorithm: **RS256** → Create
5. **Permissions** tab → **+ Add a Permission**:
   - Permission: `xaa:read` | Description: `Read access via XAA`
   - Save

---

#### F. Auth0 — Create the Regular Web App (jwt-bearer conduit)

1. Auth0 Dashboard → **Applications → Applications → + Create Application**
2. Name: `XAA Requesting Party` → Choose **Regular Web Application** → Create
3. **Settings** tab:
   - Allowed Callback URLs: `http://localhost:5000/callback`
   - Allowed Logout URLs: `http://localhost:5000`
   - Save Changes
4. Copy **Client ID** → `AUTH0_CLIENT_ID`
5. Copy **Client Secret** → `AUTH0_CLIENT_SECRET`
6. **Connections** tab → enable your Okta enterprise connection (from step D)

---

#### G. Auth0 — Create the M2M Client as a Custom API Client

> **Critical:** Create this from the **API page**, not the Applications page. Creating from the API page binds `resource_server_id` at creation — this is what Auth0 calls a "Custom API Client" and is required for the OBO token exchange.

1. Auth0 Dashboard → **Applications → APIs** → click your `XAA API`
2. Click the **Machine to Machine Applications** tab
3. Click **+ Create & Authorize New Application**
4. Name: `XAA Requesting Party — M2M` → **Create & Authorize**
5. Select scopes: check `xaa:read` → **Authorize**
6. Navigate to the new app: **Applications → Applications → XAA Requesting Party — M2M**
7. **Settings** tab → scroll to **Advanced Settings → Grant Types**
8. Enable: `urn:ietf:params:oauth:grant-type:token-exchange` → Save
9. Copy **Client ID** → `AUTH0_AGENT_CLIENT_ID`
10. Copy **Client Secret** → `AUTH0_AGENT_CLIENT_SECRET`

---

#### H. Auth0 — Create the Agent Object

1. Auth0 Dashboard → **AI** (or **Applications → AI Agents**) → **+ Create Agent**
2. Name: `XAA Agent`
3. Description: `Agent performing cross-app access on behalf of users`
4. Click **Create**
5. Copy the **Agent ID** (starts with `agt_`) → `AUTH0_AGENT_ID`

---

#### I. Auth0 — Link the Agent to the M2M Client

1. On the agent detail page (from step H)
2. Click **Link to Application** (or the **Clients** tab)
3. Select `XAA Requesting Party — M2M`
4. Confirm

This is the step that causes Auth0 to stamp `act.sub = agent_id` into OBO tokens produced by that M2M client.

---

#### Setup checklist

**Okta:**
- [ ] AI Agent app created with `ai_agent` profile → `OKTA_CLIENT_ID`, `OKTA_CLIENT_SECRET`
- [ ] Resource App (OIDC Web App) created with XAA enabled → Issuer URL = `https://your-tenant.auth0.com`
- [ ] Resource App assigned to Agent App via Resource Connections with scope `xaa:read`
- [ ] Both apps use Okta **Org AS** (`/oauth2/v1`), not Default Custom AS

**Auth0:**
- [ ] Enterprise OIDC connection pointing to Okta → `AUTH0_CONNECTION`
- [ ] API created → `AUTH0_API_AUDIENCE`
- [ ] Regular Web App created → `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`; Okta connection enabled
- [ ] M2M client created **from the API page** (Custom API Client) → `AUTH0_AGENT_CLIENT_ID`, `AUTH0_AGENT_CLIENT_SECRET`
- [ ] M2M client grant type `token-exchange` enabled (Advanced Settings → Grant Types)
- [ ] Agent object created → `AUTH0_AGENT_ID`
- [ ] Agent linked to M2M client

### 4. Provision user in Auth0 (one-time per user)

Auth0 does not support JIT provisioning via ID-JAG. Each user must log in via Auth0 Universal Login using the enterprise connection at least once to create their profile.

Click **"Step 0: Login via Auth0 (Okta)"** in the UI.

---

## Running

**Terminal 1 — API server:**
```bash
python api_server.py   # http://localhost:8080
```

**Terminal 2 — UI:**
```bash
python ui.py           # http://localhost:5000
```

**Browser:**
1. Open `http://localhost:5000`
2. Click **Step 0** (first time only) to provision your user
3. Click **Run XAA Flow**

The UI shows each token (raw + decoded payload side by side) and the final API response showing both `acting_user` and `acting_agent`.

---

## What a successful run looks like

**Terminal logs:**
```
[Step 1] Okta id_token acquired.
[Step 2] ID-JAG acquired from Okta.
[Step 3] Auth0 access token acquired.  sub=<user>
[Step 3.5] OBO delegated token acquired.  sub=<user>  act.sub=agt_xxxxxxxxxxxxxxxxxxxx
[Step 4] Calling protected API…
```

**API response:**
```json
{
  "message":      "Cross-App Access + Agent as a Principal successful!",
  "acting_user":  "okta|...",
  "acting_agent": "agt_xxxxxxxxxxxxxxxxxxxx",
  "data": [...]
}
```

