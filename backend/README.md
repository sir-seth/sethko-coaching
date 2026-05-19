# Sethko Coaching — backend

FastAPI service. Receives HealthKit data from the iOS app, pulls Whoop data,
calls Claude to generate daily coaching briefs.

## Stack

- FastAPI + uvicorn
- asyncpg + Postgres (Railway managed)
- Anthropic Python SDK (Claude Sonnet)

---

## First deploy

### 1. Create the Railway project

1. [railway.app](https://railway.app) → New Project → Deploy from GitHub repo
2. Select the `sethko-coaching` repo, set the **root directory** to `backend/`
3. Railway detects the `railway.toml` and builds with nixpacks automatically

### 2. Add Postgres

In your Railway project: **+ New** → **Database** → **Add PostgreSQL**

Railway automatically injects `DATABASE_URL` into your service. Nothing to configure.

### 3. Set environment variables

In your Railway service → **Variables** tab, add:

```
ANTHROPIC_API_KEY    = sk-ant-...
WHOOP_CLIENT_ID      = a016986e-5e66-4fc7-8e78-f2d959f6df9c
WHOOP_CLIENT_SECRET  = <your secret>
API_KEY              = <make up a strong random string — this is what the iOS app sends>
```

Generate a good `API_KEY`:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 4. Seed the database

Once the service is deployed, get the Postgres connection URL from:
**Postgres service** → **Connect** tab → copy "Postgres Connection URL"

Then run locally:

```bash
# Edit seed_users.py first — add your Whoop user IDs to WHOOP_TO_APP_USER
# (find your Whoop user_id in tokens.json)

DATABASE_URL="postgresql://..." python seed_users.py \
  --tokens-file ~/code/sethko-coaching/tokens.json
```

### 5. Set up the cron jobs

In Railway: **+ New** → **Cron** → add two jobs pointing at your service:

| Name | Schedule | HTTP method | URL |
|------|----------|-------------|-----|
| coaching-seth | `0 15 * * *` (7am PT = 15:00 UTC) | POST | `https://your-app.railway.app/coaching/generate/seth` |
| coaching-slav | `0 15 * * *` | POST | `https://your-app.railway.app/coaching/generate/slav` |

Add the header `X-API-Key: <your API_KEY>` to each cron request.

> **Note on time zones:** Railway cron runs in UTC. 7am PT = 15:00 UTC (standard time)
> or 14:00 UTC (daylight saving). Adjust seasonally, or just pick 14:00 UTC to always
> fire by 7am PT.

---

## iOS app integration

The app needs two things:

**1. Add to your `Config.swift` or equivalent:**
```swift
let backendBaseURL = "https://your-app.railway.app"
let backendAPIKey  = "<your API_KEY>"  // store in Keychain or .xcconfig, not plain source
```

**2. POST HealthKit data after each read** (add to `HealthKitManager.swift`):
```swift
func syncToBackend(snapshot: HealthSnapshot) async throws {
    let url = URL(string: "\(backendBaseURL)/health-data/seth")!
    var req = URLRequest(url: url)
    req.httpMethod = "POST"
    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    req.setValue(backendAPIKey, forHTTPHeaderField: "X-API-Key")
    req.httpBody = try JSONEncoder().encode(snapshot)
    let (_, response) = try await URLSession.shared.data(for: req)
    // handle response
}
```

**3. Fetch today's coaching brief:**
```swift
func fetchCoaching(userID: String) async throws -> CoachingResponse {
    let url = URL(string: "\(backendBaseURL)/coaching/today/\(userID)")!
    var req = URLRequest(url: url)
    req.setValue(backendAPIKey, forHTTPHeaderField: "X-API-Key")
    let (data, _) = try await URLSession.shared.data(for: req)
    return try JSONDecoder().decode(CoachingResponse.self, from: data)
}
```

---

## Local development

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Create a .env with DATABASE_URL, ANTHROPIC_API_KEY, WHOOP_CLIENT_ID,
# WHOOP_CLIENT_SECRET, API_KEY
cp .env.example .env   # then fill in values

uvicorn main:app --reload
# → http://localhost:8000
# → http://localhost:8000/docs  (auto-generated API docs)
```

---

## File structure

```
backend/
  main.py          API routes
  db.py            Postgres connection + all queries
  whoop.py         Token refresh + Whoop data pull
  coach.py         Digest builder + Claude call
  models.py        Pydantic request/response shapes
  seed_users.py    One-time DB seed + token migration
  requirements.txt
  railway.toml     Railway deploy config
  README.md
```
