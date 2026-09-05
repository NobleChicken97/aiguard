# Deployment Guide — AiGuard

Complete deployment instructions for production use, staging, and demo environments.

---

## ✨ Deployment Quick Links

- [Platform recommendation (Sep 2026)](#platform-recommendation-sep-2026)
- [Local Development](#local-development)
- [Docker Compose](#docker-compose)
- [Render/Railway (Cloud)](#renderrailway-cloud)
- [AWS/GCP Digital Ocean](#awsgcp-digital-ocean)
- [Production Checklist](#production-checklist)

---

## Platform recommendation (Sep 2026)

**Recommended: AWS with your credits — AWS App Runner + RDS PostgreSQL.**
The app ships a production Dockerfile, and App Runner builds and runs it
directly (auto TLS, health-check-driven rollout — point it at `/health`,
which already reports `db_connected`). Set `DATABASE_URL` to an RDS
PostgreSQL instance and run the idempotent cutover script
(`db/migrate_sqlite_to_pg.py`) once; Redis (`REDIS_URL`, ElastiCache) is
optional and only needed for multi-instance memory sync.

**Why not Vercel (or other serverless):** the architecture assumes a
long-lived process, which is the point of several of its safety features —

1. Paused approval turns persist in the DB (`app_pending_resumes`) with no
   thread held — but a resume must land back on the same logical session
   flow, and serverless cold starts + function timeouts still fight the
   multi-minute agent turns around it.
2. Rate limiters (`webapp_ratelimit.py`) are in-process; serverless fans
   out to ephemeral instances, so per-IP caps silently stop working.
3. `/api/stream` is a long-lived SSE connection; agent turns run many
   seconds to minutes.
4. SQLite lives on a local disk that serverless filesystems reset on every
   instance — you'd be forced onto a managed DB anyway.

Containers-on-a-platform (App Runner, or ECS Fargate / Render / Railway)
keep every one of those behaviors intact. Vercel is only a fit if the
frontend is split into a static site — unnecessary here, since FastAPI
serves the Jinja UI itself.

---

## ☁️ EC2 Free-Tier Single Box (EXECUTED plan)

$0 path when App Runner is unavailable (e.g. an account without an App
Runner subscription): one `t3.micro` (Amazon Linux 2023, 30 GB gp3 — both
free-tier) running the existing `docker-compose.yml` plus
`docker-compose.prod.yml` (Caddy TLS + restart policies). Postgres + Redis
run in compose; no RDS involved.

- Entry files: `Caddyfile`, `docker-compose.prod.yml`,
  `deploy/ec2-user-data.sh` (placeholders `__DOMAIN__`,
  `__LLM_API_KEY__`, `__SESSION_SECRET__` filled at launch; the rendered
  script with secrets is never committed).
- Access: SSM Session Manager only (no SSH keys, no port 22). SG opens
  80/443 to the world; Elastic IP stays attached (free while attached).
- DNS: Namecheap A record for the hostname → Elastic IP; Caddy issues
  certs automatically once it resolves (first boot retries on its own).
- Secrets live only in the box `.env` (root-readable, never in the repo);
  rotate provider keys after pasting them through chat.
- After boot: `docker compose exec -T app python -m db.seed` seeds demo
  rows (schema auto-creates); verify `/health`, then the `docs/DEMO.md`
  walkthrough on the domain.

---

## ☁️ AWS App Runner + RDS PostgreSQL (RECOMMENDED setup guide)

No local Docker needed: App Runner builds straight from the GitHub repo
(source deploy), and the migration script runs from your machine. Console
values below are exact — no `apprunner.yaml` file required (everything is
set in the console, which is also where secrets belong).

### Part 0 — Two console prerequisites (≈3 min, you — the CLI cannot do these)

Our automation verified (Sep 2026) that this account's CLI key gets
`SubscriptionRequiredException` on every App Runner call until the console
onboarding completes, and GitHub source deploys additionally need an OAuth
handshake. Both happen in the browser, once ever:

1. Open the **App Runner console** (same region you'll deploy in,
   e.g. `ap-south-1`). If it shows a first-run / onboarding / "get started"
   prompt, accept it. (This clears the subscription error; the
   `AWSServiceRoleForAppRunner` service-linked role already exists.)
2. When you reach Part C below, App Runner will offer **Add new GitHub
   connection** inline → **Install AWS Connector for GitHub** → authorize
   `NobleChicken97` (all repos, or select `agentic_guardrails`) → you land
   back in the console with the connection ready. No CLI equivalent exists
   for this handshake.

### Part A — RDS PostgreSQL (~10 min, console)

1. RDS → **Create database** → Engine: **PostgreSQL 16.x** → Templates:
   **Free tier** (or `db.t4g.micro`, 20 GB gp3 — covered by credits either way).
2. DB instance identifier: `agentic-postgres`. Master username: `admin`
   (dedicated app user landing later; master is fine for initial setup).
   Master password: generate a long one, **save it in a password manager**.
3. **Public access: Yes** (setup simplicity — see hardening note below).
   Create a **new security group**; after creation, edit its inbound rule:
   PostgreSQL/5432 source = **your IP only** (console offers "My IP").
4. Additional configuration → Initial database name: `agentic_db`.
   Create. Wait for Status **Available** (~5–10 min).
5. Copy the **Endpoint** (looks like
   `agentic-postgres.xxxxx.us-east-1.rds.amazonaws.com`). Your URL is:
   `postgresql://admin:YOUR-PASSWORD@ENDPOINT:5432/agentic_db`

> Hardening note (demo-acceptable as configured): public RDS + strong
> password + IP-scoped security group + psycopg2 SSL-by-default is reasonable
> for a demo. For anything real: private RDS + App Runner VPC connector +
> Secrets Manager rotation (see Secrets Management above).

### Part B — Migrate + seed (~5 min, your terminal)

```powershell
$env:DATABASE_URL = "postgresql://admin:YOUR-PASSWORD@ENDPOINT:5432/agentic_db"
python -m db.migrate_sqlite_to_pg --source data/guardrails.db
```

Expect per-table `source == target` counts ending in `Verification passed`.
This copies schema + demo rows + app rows (13 tables, FK-safe order). The
`--truncate` flag is only for clean re-cutovers.

### Part C — App Runner service (~10 min, console)

1. App Runner → **Create service** → Source: **Source code repository** →
   connect GitHub (`NobleChicken97/aiguard`, branch `main`).
   Deployment trigger: **Automatic** (every push redeploys).
2. Build settings → Runtime: **Python 3.11** → Build command:
   `pip install -r requirements.txt` → Start command:
   `uvicorn webapp:app --host 0.0.0.0 --port 8000` → Port: **8000**.
3. Health check → Path: **`/health`** (keep the rest default).
4. Instance: **0.25 vCPU / 0.5 GB** (demo-sufficient, credit-cheap).
5. Environment variables (paste; mark `LLM_API_KEY` as **Secret**):

   | Key | Value |
   |---|---|
   | `DATABASE_URL` | the `postgresql://…` URL from Part A |
   | `LLM_PROVIDER` | `groq` |
   | `LLM_API_KEY` | your Groq key (secret) |
   | `SESSION_SECRET` | fresh output of `python -c "import secrets;print(secrets.token_hex(32))"` |
   | `SESSION_COST_BUDGET_USD` | `0.50` |
   | `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json` (CloudWatch-friendly) |

6. Create + wait for Status **Running** (~5 min first build — watch the
   build log; `pip install` pulls spaCy + the NER model wheel).

### Part D — Verify live (~10 min, browser)

1. Open the App Runner URL → `/health` returns
   `{"status":"ok","db_connected":true,…}` (**against RDS**, not SQLite).
2. Register two users (normal + incognito), run the `docs/DEMO.md` shots:
   normal query → DROP refusal → approval flow → PII masking → Bob 404s on
   Alice's trace.
3. Persistence proof: trigger a redeploy (push an empty commit or hit
   **Deploy**), then confirm sessions/traces/memory survived — that is the
   architecture working outside local Docker.
4. Record the URL + date + smoke results; send them back to be filed as
   the v1.7.0 evidence entry (same style as every other entry).

---

## 🚀 Local Development

### Prerequisites
- Python 3.9+
- Pip package manager
- Docker (optional, for containers)

### Setup Steps

```bash
# 1. Clone repository
git clone <repository-url>
cd "Agentic system with safety guardrails"

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env and add: ANTHROPIC_API_KEY="sk-ant-..."

# 5. Run the application
python main.py  # Interactive mode
```

### Interactive Mode Options
```bash
# Single prompt mode
python main.py --prompt "What is 15 times 37?"

# Custom user ID
python main.py --user-id johndoe

# Demo mode (no API key needed)
python main.py --demo

# CLI approval mode
python main.py --approval-mode cli

# Auto-approve for demos
python main.py --approval-mode auto-approve

# Auto-deny for safety
python main.py --approval-mode auto-deny
```

### Verify Installation
```bash
# Run health check
http://localhost:8000/health

# Expected response:
{
  "status": "ok",
  "db_connected": true,
  "session_count": 0
}
```

---

## 🐳 Docker Compose

### Completion Status: ✅ Implemented

### Why Docker?
- Consistent environment across development and production
- Isolated dependencies (no local Python conflicts)
- Easy scaling and deployment

### Prerequisites
- Docker Desktop (Windows/Mac) or Docker Engine (Linux)
- Docker Compose v2.0+

### Quick Deploy

```bash
# 1. Clone repository
git clone <repository-url>
cd "Agentic system with safety guardrails"

# 2. Configure environment
cp .env.example .env
# Add ANTHROPIC_API_KEY

# 3. Start services
docker-compose up --build

# 4. Access at http://localhost:8000
```

### Docker Compose Configuration

**File**: `docker-compose.yml`

```yaml
version: '3.8'

services:
  api:
    build: .
    container_name: netsentry_api
    ports:
      - "8000:8000"
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - DB_PATH=${DB_PATH:-/app/data/guardrails.db}
      - CLAUDE_MODEL=${CLAUDE_MODEL:-claude-sonnet-4-20250514}
      - SESSION_COST_BUDGET_USD=${SESSION_COST_BUDGET_USD:-0.50}
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

networks:
  default:
    name: netsentry_network
```

### Docker Management Commands

```bash
# Start services
docker-compose up -d

# View logs
docker-compose logs -f api

# Restart services
docker-compose restart

# Stop services
docker-compose down

# Rebuild (after code changes)
docker-compose up --build

# Database backup
docker-compose exec api cp /app/data/guardrails.db /app/data/guardrails.db.backup
```

### Docker Production Notes
- **Persistent Storage**: Map `./data` volume for DB persistence
- **Logging**: Map `./logs` for application logs
- **Resource Limits**: Add `deploy: resources: limits:` for container management
- **Secrets**: Use Docker secrets for sensitive values (not shown here)

---

## 🚀 Render/Railway Cloud Deployment

### Completion Status: ✅ Documented

### Why Render/Railway?
- Free tier limits suitable for demos
- Automatic deployments from GitHub
- SSL certificates included
- PostgreSQL database integration
- Built-in logging and monitoring

### Deployment Steps

#### Option 1: Render (Free Tier)

1. **Prepare Repository**
   ```bash
   # Ensure all files are committed
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin <your-github-repo-url>
   git push -u origin main
   ```

2. **Create Render Account**
   - Visit https://render.com
   - Connect GitHub account

3. **Create Web Service**
   - Click "New +"
   - Select "Web Service"
   - Connect to your repository
   - Configure:
     - **Name**: `netsentry-ai-agent`
     - **Region**: `Oregon (us-west-2)` (closest to your users)
     - **Environment**: `Python 3`
     - **Build Command**: Leave empty (auto-detected)
     - **Start Command**: `uvicorn webapp:app --host 0.0.0.0 --port $PORT`

4. **Set Environment Variables**
   - `ANTHROPIC_API_KEY`: Your Claude API key
   - `DEMO_MODE`: Leave empty
   - (Optional) `SESSION_COST_BUDGET_USD`: `0.10` for safety

5. **Configure Database** (Optional)
   - Use Render Postgres for persistent storage
   - Connection string: `sqlite:////data/postgres.sqlite`

6. **Deploy**
   - Click "Create Web Service"
   - Render automatically:
     - Clones repository
     - Installs dependencies
     - Deploys the application
     - Exposes via HTTPS

7. **Access Production**
   - URL format: `<your-app-name>.up.railway.app`
   - Deployments take ~3-5 minutes
   - Monitor at `https://dashboard.render.com`

#### Option 2: Railway (Free Tier)

1. **Prepare Repository**
   ```bash
   # Git setup as above
   ```

2. **Create Railway Account**
   - Visit https://railway.app
   - Connect GitHub

3. **Create App**
   - Click "New Project"
   - Select "New Project from GitHub"
   - Choose your repository

4. **Configure**
   - **Language**: Python
   - **Build Command**: No build command needed
   - **Start Command**: `uvicorn webapp:app --host 0.0.0.0 --port $PORT`

5. **Add Variables**
   - `ANTHROPIC_API_KEY`
   - `SESSION_COST_BUDGET_USD`: `0.10`

6. **Deploy**
   - Railway automatically detects and deploys
   - URL: `<random-name>.railway.app`

### Port Configuration

Both Render and Railway set `$PORT` environment variable automatically:

```python
# webapp.py
import os
PORT = int(os.getenv("PORT", "8000"))

app = FastAPI(port=PORT)  # Use configured port
```

### Database Persistence

#### Render Postgres
```yaml
# In Render dashboard
Settings → Variables → Add variable:
DATABASE_URL=postgresql://user:pass@host:5432/netsentry_db
```

Update `config.py`:
```python
import os
DATABASE_URL = os.getenv("DATABASE_URL", "data/guardrails.db")
```

Run migrations:
```bash
# On deployment
python -c "from db.database import initialize_db; import sqlite3; sqlite3.connect('data/guardrails.db')"
```

#### SQLite (simpler, no migrations)
- Use local SQLite for deployment simplicity
- Volume persistence via Render file storage
- Performance adequate for demo/prototype

---

## ☁️ AWS/GCP Digital Ocean Deployment

### AWS Elastic Beanstalk

#### Prerequisites
- AWS Account
- Existing S3 bucket or EFS volume for database storage
- CLI (`aws-cli`) installed

#### Steps

1. **Create Application**
   ```bash
   # Create config file .ebextensions/
   mv tests/environment.config .ebextensions/environment.config
   ```

   ```ini
   # .ebextensions/environment.config
   option_settings:
     - namespace: aws:elasticbeanstalk:application:environment
       option_name: ANTHROPIC_API_KEY
       value: YOUR_API_KEY
     - namespace: aws:elasticbeanstalk:application:environment
       option_name: SESSION_COST_BUDGET_USD
       value: "0.10"
   ```

2. **Deploy**
   ```bash
   eb init netsentry-api
   eb create netsentry-prod
   eb deploy
   ```

3. **Access**
   - URL: `http://netsentry-prod.xxxxx.elasticbeanstalk.com`

### Google Cloud GKE

#### Prerequisites
- Google Cloud Platform account
- Kubernetes cluster

#### Steps

1. **Build Container**
   ```bash
   gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/netsentry-ai-agent
   ```

2. **Deploy to GKE**
   ```bash
   kubectl apply -f k8s/deployment.yaml
   kubectl apply -f k8s/service.yaml
   ```

#### Kubernetes Configuration (`k8s/deployment.yaml`)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: netsentry-api
spec:
  replicas: 2
  selector:
    matchLabels:
      app: netsentry-api
  template:
    metadata:
      labels:
        app: netsentry-api
    spec:
      containers:
        - name: netsentry-api
          image: gcr.io/YOUR_PROJECT_ID/netsentry-ai-agent
          ports:
            - containerPort: 8000
          env:
            - name: ANTHROPIC_API_KEY
              valueFrom:
                secretKeyRef:
                  name: netsentry-secrets
                  key: anthropic-api-key
          resources:
            limits:
              memory: "512Mi"
              cpu: "500m"
---
apiVersion: v1
kind: Service
metadata:
  name: netsentry-api-service
spec:
  selector:
    app: netsentry-api
  ports:
    - protocol: TCP
      port: 80
      targetPort: 8000
```

### Digital Ocean App Platform

#### Steps

1. **Create App**
   - Use Digital Ocean App Platform dashboard
   - Connect GitHub repository
   - Configure app settings

2. **Build Configuration**
   - **Build Command**: No
   - **Run Command**: `uvicorn webapp:app --host 0.0.0.0`

3. **Environment Variables**
   - Add `ANTHROPIC_API_KEY`
   - Add `SESSION_COST_BUDGET_USD=0.10`

4. **Deploy**
   - Digital Ocean automatically creates staging and production

---

## 🔐 Secrets Management

`.env` is local-dev only (gitignored — never commit it). Every secret the
app reads, grouped by blast radius:

| Secret | Blast radius if leaked | Rotation |
|---|---|---|
| `ANTHROPIC_API_KEY` / `LLM_API_KEY` | Provider spend on your card | Regenerate in provider console, redeploy |
| `SESSION_SECRET` | Forged login cookies for all users | Change → all sessions invalidate (stateless cookies) |
| `DATABASE_URL` (prod) | Full read/write on all app + demo data | Rotate RDS creds + redeploy |

Rules: secrets travel as environment variables in every supported target
(compose `env_file`, App Runner / Render / Railway secret stores) — never
baked into the image, never logged (the JSON logger redacts nothing
automatically, so never `log.info` a config object). Generate secrets with
`python -c "import secrets; print(secrets.token_hex(32))"`.

Managed path (recommended on AWS): store the three values in AWS Secrets Manager, attach them as App Runner secrets (they land as env vars, no code
changes — the app only ever reads `os.getenv`). Doppler / 1Password
Connect follow the same shape: sync secrets → env → process. Wiring a
provider SDK into the app itself is deliberately deferred (Phase 6 scope
was the documented path, not the integration).

---

## ✅ Production Checklist

### Security

- [ ] **API Keys** stored securely (environment variables, secrets management)
- [ ] .env file in `.gitignore` (verified in repository)
- [ ] HTTP security headers enabled (CORS, HSTS, CSP)
- [ ] Rate limiting configured
- [ ] Request validation in place (FastAPI Pydantic models)

### Reliability

- [ ] Health check endpoint configured
- [ ] Database backup routine established
- [ ] Error logging to file/cloud (monitoring)
- [ ] Max session iterations set (prevents runaway loops)
- [ ] Session cost budgeting enabled
- [ ] Automatic restart policies (deployment platform)

### Performance

- [ ] Database connection pooling configured
- [ ] CDN for web assets (if UI contains images)
- [ ] Response caching implemented (FastAPI `@cache`)
- [ ] Load testing conducted (target: 100 concurrent users)

### Compliance

- [ ] Private PII data masked in traces (if applicable)
- [ ] Audit logging enabled
- [ ] GDPR/CCPA compliance considered
- [ ] Security incident response plan documented

### Documentation

- [ ] README.md updated with deployment steps
- [ ] API documentation accessible
- [ ] Troubleshooting guide available
- [ ] Documentation URL created (e.g., `docs.netsentry.ai`)

### Monitoring

- [ ] Application uptime monitoring
- [ ] Error rate alerting configured
- [ ] Database performance tracking
- [ ] API token usage monitoring
- [ ] Cost/spend alerts (Anthropic API + hosting)

### Backups

- [ ] Database backup schedule (e.g., daily at 2 AM)
- [ ] Backup retention policy (e.g., 30 days)
- [ ] Backup testing performed monthly
- [ ] Disaster recovery plan documented

---

## 🔧 Customization

### Adjusting Safety Limits

```bash
# .env
# RISKY_ROW_THRESHOLD - when UPDATE/DELETE requires approval
RISKY_ROW_THRESHOLD=10

SESSION_COST_BUDGET_USD - max cost per session
SESSION_COST_BUDGET_USD=0.05

```

### Adding Sign-up/Authentication

```python
# webapp.py
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from passlib.context import CryptContext

# Add authentication endpoints
@app.post("/token")
def login(OAuth2PasswordRequestForm = Depends()):
    # Replace with real auth backend
    pass

# Protect chat endpoint
@app.post("/api/chat")
def chat_api(req: ChatRequest, current_user: User = Depends(get_current_user)):
    # Add authentication check
    pass
```

### Customizing Guardrail Rules

Edit `guardrails/sql_guardrail.py`:

```python
# BLOCK_STATEMENT_TYPES - enhance rule set
BLOCK_STATEMENT_TYPES = {
    exp.Drop: "DROP",
    # Add more as needed
    exp.Delete: "DELETE",
    exp.Alter: "ALTER",
}

# Allow more tables (if appropriate for use case)
ALLOWED_TABLES = {"customers", "orders", "products", "logs", "audit"}
```

---

## 🗄️ PostgreSQL Production Database

### Completion Status: ✅ Implemented (+ integration tests)

The data layer is dialect-aware (`db/database.py`). By default everything runs on SQLite; setting
`DATABASE_URL` to a postgres URL transparently switches all persistence (sessions, messages,
tool calls, approvals, facts, traces) to a pooled PostgreSQL connection.

```env
# .env — leave unset for local SQLite
DATABASE_URL=postgresql://app_user:secret@localhost:5432/guardrails
```

### How It Works
- `get_connection()` returns either a raw sqlite3 connection or a `PGConnectionWrapper`
  around a psycopg2 pool connection (`SimpleConnectionPool(1, 20)`).
- The wrapper translates `?` placeholders to `%s` and mimics dict-row access.
- `initialize_db()` translates the schema (`INTEGER PRIMARY KEY` → `SERIAL`, drops `AUTOINCREMENT`).
- Dialect differences are handled internally: e.g. idempotent inserts use `INSERT OR IGNORE`
  on SQLite and `ON CONFLICT DO NOTHING` on PostgreSQL.

### Switching an Existing Deployment
1. Provision the database and user; the app creates its own tables on startup.
2. Set `DATABASE_URL`, restart — schema is initialized automatically.
3. Migrate existing SQLite history with the bundled one-shot script (below).

### Migrating Existing SQLite Data

`db/migrate_sqlite_to_pg.py` copies all app tables (sessions, messages, tool
calls, approvals, memory facts, trace events) and demo data from SQLite into
the configured `DATABASE_URL` target:

```bash
# merge mode (default): idempotent, safe to re-run; existing rows are skipped
DATABASE_URL=postgresql://app_user:secret@localhost:5432/guardrails \
    python -m db.migrate_sqlite_to_pg

# clean cutover: empty the target tables first
DATABASE_URL=postgresql://app_user:secret@localhost:5432/guardrails \
    python -m db.migrate_sqlite_to_pg --truncate

# explicit source file (defaults to config.DB_PATH)
python -m db.migrate_sqlite_to_pg --source /path/to/guardrails.db
```

Behavior details:
- Tables copy parents-before-children so foreign keys stay satisfied.
- Inserts use `ON CONFLICT DO NOTHING`, so re-runs never duplicate rows.
- SERIAL sequences behind `INTEGER PRIMARY KEY` demo tables are advanced past
  the migrated ids, so post-migration inserts cannot collide.
- Finishes with a per-table source/target row-count report and exits nonzero
  on any mismatch.

The script is covered by `tests/test_migration_script.py` (auto-skips without
`TEST_DATABASE_URL`; runs in CI against the postgres service container).

### Verifying the PostgreSQL Path Locally
Integration tests run only when a disposable test instance is provided:

```bash
docker run --rm -d -p 5432:5432 -e POSTGRES_PASSWORD=test postgres:16
# PowerShell:
$env:TEST_DATABASE_URL = "postgresql://postgres:test@localhost:5432/postgres"
pytest tests/test_postgres_integration.py tests/test_migration_script.py -v
```

These tests specifically cover the failure modes that only appear on PostgreSQL
(dialect-specific upserts, rowcount semantics, executemany seeding, sequence
resync) that the SQLite suite cannot catch. CI runs them automatically against
a postgres:16 service container (`.github/workflows/ci.yml`).

---

## 📊 Environment Variable Reference

```env
# LLM Provider (Required)
ANTHROPIC_API_KEY=sk-ant-...  # Your Claude API key
CLAUDE_MODEL=claude-sonnet-4-20250514  # Model name (default)

# Database Configuration
DB_PATH=data/guardrails.db  # SQLite file path (used when DATABASE_URL is unset)
DATABASE_URL=  # Optional; set to a postgres:// URL to use PostgreSQL instead of SQLite
REDIS_URL=redis://localhost:6379/0  # Optional; short-term memory sync (graceful fallback if unreachable)
WORKER_MAX_ITERATIONS=5  # Max LLM iterations per worker task

# Safety & Limits
MAX_RETRIES=3  # Max retry attempts for tool failures
BACKOFF_BASE_SECONDS=1.0  # Base backoff delay (seconds)
RISKY_ROW_THRESHOLD=5  # Row count requiring approval
SESSION_COST_BUDGET_USD=0.50  # Max session cost (prevents runaway)

# Model Configuration
PROJECT_ROOT=/app  # Project directory for path resolution
SYSTEM_PROMPT=/app/agent/system_prompt.txt  # Custom system prompt

# Security
ALLOWED_TABLES=customers,products,orders,order_items  # Table whitelist

# Deployment
PORT=8000  # Server port (default)
ENV=production  # Environment (development/production)
```

---

## 🐛 Troubleshooting

### Port Already in Use
```bash
# Kill process on port 8000
lsof -ti:8000 | xargs kill -9
# Or use different port
JAVA_OPTS="-Dserver.port=8001" uvicorn webapp:app
```

### Database Access Denied
```bash
# Check DB permissions
chmod 644 data/guardrails.db

# Ensure directory exists
mkdir -p data
```

### Claude API Key Issues
```bash
# Verify API key format
curl https://api.anthropic.com/v1/messages \
  -H "x-api-key: sk-ant-..." \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-sonnet-4-20250514","max_tokens":10}'
```

### Connection Refused
```bash
# Verify service is running
curl http://localhost:8000/health

# Check logs
docker-compose logs -f api
```

---

## 📈 Scaling & Performance

### Horizontal Scaling

For production systems needing more capacity:

1. **Auto-scaling** (Render/Railway):
   - Configure resource allocation automatically based on traffic

2. **Manual Scaling** (Docker Swarm):
   ```bash
   docker-compose up --scale api=4
   ```

3. **Load Balancing**:
   - Place multiple instances behind a load balancer (Nginx, HAProxy)

### Database Optimization

For high traffic:
- Migrate from SQLite to PostgreSQL
- Implement connection pooling
- Add read replicas for queries
- Configure query caching

---

## 🎓 Production Experience

### Initial Deployment (First Week)
1. Monitor error logs daily
2. Track API token consumption
3. Check for suspicious failed queries
4. Adjust safety limits based on user behavior

### Regular Maintenance
- Weekly: Review unused tables and accounts
- Monthly: Test database backups
- Quarterly: Review security logs (if logging enabled)
- Annually: Update dependencies, test new Claude model versions

---

## 📞 Support & Documentation

| Resource | Link |
|----------|------|
| Main README | [README.md](../README.md) |
| Test Results | [report.md](report.md) |
| API Documentation | [../README.md](../README.md) |
| Issue Tracker | GitHub Issues |
| Docs Site | (Optional: generated via MkDocs/DocFX) |

---

**Last Updated**: August 21, 2026
**Status**: ✅ Deployment procedures validated and documented

*This agentic system is production-ready. Follow these deployment steps for reliable, safe execution in your environment.*