# DevSecOps Autopilot

> AI-powered business logic vulnerability detection for GitLab merge requests, built with Google ADK + GitLab MCP.

---

## Architecture

```
Developer opens/updates MR
          │
          ▼
   GitLab Webhook ──────────────────────────────────────────────┐
                                                                 │ POST /webhook
          ┌──────────────────────────────────────────────────────▼──┐
          │              Cloud Run (this service)                    │
          │  • Validates X-Gitlab-Token                              │
          │  • Dispatches agent as background task                   │
          └──────────────────────────────────────────────────────────┘
                                    │
                                    ▼
          ┌──────────────────────────────────────────────────────────┐
          │           Google ADK Runner (async)                      │
          │  • Gemini 2.5 Flash reasons about business logic         │
          │  • McpToolset connects to GitLab MCP over stdio/npx      │
          └──────────────────────────────────────────────────────────┘
                    │  reads                       │  writes
                    ▼                              ▼
          GitLab MCP Server               GitLab MCP Server
          • get_merge_request_diffs       • create_merge_request_note
          • get_merge_request             • update_merge_request (labels)
          • list_project_pipelines        • create_issue
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GITLAB_PAT` | ✅ | GitLab Personal Access Token |
| `GITLAB_WEBHOOK_SECRET` | ✅ | Secret token configured in the GitLab webhook |
| `PORT` | optional | Server port (default: `8080`) |

---

## Getting a GitLab PAT

1. GitLab → **User Settings** → **Access Tokens** → **Add new token**
2. Scopes required: `api`, `read_repository`, `write_repository`
3. Copy the token — you'll only see it once

---

## Local Setup

```bash
# 1. Clone the repo
git clone <repo-url>
cd devsecops-autopilot

# 2. Create your .env
cp .env.example .env
# Fill in GITLAB_PAT and GITLAB_WEBHOOK_SECRET

# 3. Install dependencies (Python 3.11+)
pip install -r requirements.txt

# 4. Ensure Node.js is available (for mcp-remote)
node --version   # must be >= 18

# 5. Run
python main.py
```

Health check: `curl http://localhost:8080/health`

---

## Configuring the GitLab Webhook

1. In your GitLab project: **Settings → Webhooks → Add new webhook**
2. **URL:** `https://<your-cloud-run-url>/webhook`
3. **Secret token:** same value as `GITLAB_WEBHOOK_SECRET`
4. **Trigger:** ✅ Merge request events
5. **SSL verification:** ✅ enabled

---

## Deploying to Cloud Run

### Prerequisites

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com
```

### Store secrets in Secret Manager

```bash
echo -n "your_gitlab_pat" | gcloud secrets create gitlab-pat --data-file=-
echo -n "your_webhook_secret" | gcloud secrets create gitlab-webhook-secret --data-file=-
```

### Deploy via Cloud Build

```bash
gcloud builds submit --config cloudbuild.yaml
```

Or manually:

```bash
gcloud run deploy devsecops-autopilot \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets=GITLAB_PAT=gitlab-pat:latest,GITLAB_WEBHOOK_SECRET=gitlab-webhook-secret:latest
```

---

## What the Agent Detects

The agent specialises in **business logic flaws** — vulnerabilities that are syntactically correct code that violates business rules. SAST tools like Semgrep cannot detect these.

| Category | Examples |
|---|---|
| Financial Logic | Over-refunds, negative discount exploits, double-credit |
| State Machine | Acting on shipped orders, skipping workflow steps |
| Authorization | Role self-escalation, missing ownership checks |
| Limit Bypass | Frontend-only enforcement, quota circumvention |
| SSRF via Features | Webhook URLs accepting internal IPs |

---

## License

MIT
