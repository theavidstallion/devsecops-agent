"""
DevSecOps Autopilot — Agent Definition
=======================================
Uses direct GitLab REST API calls instead of the MCP stdio toolset.

WHY: McpToolset + StdioConnectionParams spawns a node child process and performs
a full MCP handshake every time tools are needed inside an asyncio background
task. In Cloud Run this is fragile:
  - apt-installed Node.js may be too old for the MCP package
  - The asyncio TaskGroup inside the MCP session manager conflicts with
    FastAPI's background-task event loop semantics
  - Any node crash silently leaves the tool list empty, causing:
      ValueError: Tool 'get_merge_request_diffs' not found

These plain async Python functions are called directly by ADK with no
subprocess, no handshake, and no external binary dependency.
"""

import logging
import os
from typing import Any

import httpx
from google.adk.agents import Agent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GITLAB_INSTANCE_URL = "gitlab.com"
GITLAB_API_BASE = f"https://{GITLAB_INSTANCE_URL}/api/v4"
GITLAB_PAT = os.getenv("GITLAB_PAT", "")

# ---------------------------------------------------------------------------
# GitLab REST API helper
# ---------------------------------------------------------------------------

def _headers() -> dict:
    return {
        "PRIVATE-TOKEN": GITLAB_PAT,
        "Content-Type": "application/json",
    }


async def _get(path: str, params: dict | None = None) -> Any:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{GITLAB_API_BASE}{path}", headers=_headers(), params=params)
        r.raise_for_status()
        return r.json()


async def _post(path: str, json: dict) -> Any:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{GITLAB_API_BASE}{path}", headers=_headers(), json=json)
        r.raise_for_status()
        return r.json()


async def _put(path: str, json: dict) -> Any:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.put(f"{GITLAB_API_BASE}{path}", headers=_headers(), json=json)
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# Tool functions (registered directly with ADK)
# ---------------------------------------------------------------------------

async def get_merge_request_diffs(project_id: int | str, mr_iid: int | str) -> dict:
    """Retrieve the file-level diffs for a GitLab merge request.

    Args:
        project_id: GitLab project ID (integer) or URL-encoded namespace/path.
        mr_iid:     The merge request internal ID (the !N number shown in the UI).

    Returns:
        A dict with keys:
          - diffs: list of diff objects, each with 'old_path', 'new_path',
                   'diff' (unified diff text), 'new_file', 'renamed_file',
                   'deleted_file'.
          - overflow: bool, true if the diff was truncated by GitLab.
    """
    try:
        data = await _get(
            f"/projects/{project_id}/merge_requests/{mr_iid}/diffs",
            params={"per_page": 100},
        )
        return {"diffs": data, "overflow": False}
    except httpx.HTTPStatusError as e:
        logger.error("get_merge_request_diffs failed: %s", e)
        return {"error": str(e), "diffs": [], "overflow": False}


async def get_merge_request(project_id: int | str, mr_iid: int | str) -> dict:
    """Retrieve metadata for a GitLab merge request.

    Args:
        project_id: GitLab project ID or URL-encoded namespace/path.
        mr_iid:     The merge request internal ID.

    Returns:
        A dict with keys including: title, description, state, author,
        labels, target_branch, source_branch, web_url, and pipeline info.
    """
    try:
        return await _get(f"/projects/{project_id}/merge_requests/{mr_iid}")
    except httpx.HTTPStatusError as e:
        logger.error("get_merge_request failed: %s", e)
        return {"error": str(e)}


async def list_project_pipelines(project_id: int | str, ref: str | None = None) -> dict:
    """List the most recent pipelines for a GitLab project.

    Args:
        project_id: GitLab project ID or URL-encoded namespace/path.
        ref:        Optional branch/tag name to filter pipelines (e.g. 'main').

    Returns:
        A dict with key 'pipelines': a list of pipeline objects, each with
        id, status, ref, sha, web_url, and created_at.
    """
    try:
        params: dict = {"per_page": 5, "order_by": "id", "sort": "desc"}
        if ref:
            params["ref"] = ref
        data = await _get(f"/projects/{project_id}/pipelines", params=params)
        return {"pipelines": data}
    except httpx.HTTPStatusError as e:
        logger.error("list_project_pipelines failed: %s", e)
        return {"error": str(e), "pipelines": []}


async def create_merge_request_note(
    project_id: int | str, mr_iid: int | str, body: str
) -> dict:
    """Post a comment (note) on a GitLab merge request.

    Args:
        project_id: GitLab project ID or URL-encoded namespace/path.
        mr_iid:     The merge request internal ID.
        body:       The markdown-formatted comment text to post.

    Returns:
        A dict with the created note's id, body, author, and created_at.
    """
    try:
        return await _post(
            f"/projects/{project_id}/merge_requests/{mr_iid}/notes",
            json={"body": body},
        )
    except httpx.HTTPStatusError as e:
        logger.error("create_merge_request_note failed: %s", e)
        return {"error": str(e)}


async def update_merge_request(
    project_id: int | str,
    mr_iid: int | str,
    labels: str | None = None,
    title: str | None = None,
    description: str | None = None,
) -> dict:
    """Update a GitLab merge request (labels, title, description, etc.).

    Args:
        project_id:  GitLab project ID or URL-encoded namespace/path.
        mr_iid:      The merge request internal ID.
        labels:      Comma-separated label names to set on the MR.
                     REPLACES the existing label list entirely.
                     Example: "security::critical,needs-fix"
        title:       Optional new title for the MR.
        description: Optional new description for the MR.

    Returns:
        A dict with the updated merge request object.
    """
    payload: dict = {}
    if labels is not None:
        payload["labels"] = labels
    if title is not None:
        payload["title"] = title
    if description is not None:
        payload["description"] = description

    if not payload:
        return {"error": "No fields to update provided."}

    try:
        return await _put(
            f"/projects/{project_id}/merge_requests/{mr_iid}",
            json=payload,
        )
    except httpx.HTTPStatusError as e:
        logger.error("update_merge_request failed: %s", e)
        return {"error": str(e)}


async def create_issue(
    project_id: int | str,
    title: str,
    description: str,
    labels: str | None = None,
) -> dict:
    """Create a new issue in a GitLab project.

    Args:
        project_id:  GitLab project ID or URL-encoded namespace/path.
        title:       Issue title.
        description: Issue description (markdown supported).
        labels:      Optional comma-separated label names.
                     Example: "security,needs-fix"

    Returns:
        A dict with the created issue's id, iid, title, web_url, and labels.
    """
    payload: dict = {"title": title, "description": description}
    if labels:
        payload["labels"] = labels

    try:
        return await _post(f"/projects/{project_id}/issues", json=payload)
    except httpx.HTTPStatusError as e:
        logger.error("create_issue failed: %s", e)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
You are DevSecOps Autopilot, an expert AI security engineer specializing
in business logic vulnerability detection for web APIs and microservices.

You are triggered when a developer opens or updates a merge request in GitLab.

YOUR WORKFLOW — execute these steps in strict order every single time:

STEP 1 — RETRIEVE MR DIFF
Call get_merge_request_diffs with the provided project_id and mr_iid.
Read the full diff carefully. Note every changed file and the nature of each change.

STEP 2 — RETRIEVE CONTEXT
Call get_merge_request with project_id and mr_iid to get title, description,
author, and any linked issue references.
Call list_project_pipelines with project_id to get the latest pipeline status.

STEP 3 — ANALYZE FOR BUSINESS LOGIC FLAWS
You are NOT looking for syntax errors, known CVEs, or dependency vulnerabilities.
The pipeline SAST tools already handle those.

You ARE looking for flaws where code is syntactically correct but violates
business rules. Hunt specifically for:

FINANCIAL LOGIC FLAWS:
- Refund/credit amounts not validated against original transaction amounts
- Negative value exploits in financial calculations
- Double-charge or double-credit scenarios due to missing idempotency
- Fee calculation bypasses

STATE MACHINE VIOLATIONS:
- Actions on resources in invalid states (capturing an already-captured
  transaction, cancelling a shipped order, reopening a resolved dispute)
- Missing status transition validation
- Workflow steps that can be skipped

AUTHORIZATION GAPS:
- User-controlled parameters that affect role or permission assignment
- Missing ownership checks (can user A modify user B's resources?)
- Horizontal privilege escalation via predictable IDs
- Role escalation via request body parameters

LIMIT BYPASS:
- Business tier limits enforced only on frontend, not validated server-side
- Rate limit bypasses through parameter manipulation
- Quota checks that can be circumvented

SSRF VIA BUSINESS FEATURES:
- Webhook URL registration accepting internal network addresses
- Avatar/import URLs accepting 169.254.x.x, 10.x.x.x, localhost, 127.x.x.x
- No URL allowlist validation on user-supplied URLs

STEP 4 — POST MR COMMENT
Call create_merge_request_note with project_id and mr_iid.

Format the comment body EXACTLY like this markdown:

---
## 🔍 DevSecOps Autopilot — Business Logic Security Review

**Pipeline Status:** [pass/fail + one line summary]
**Business Logic Findings:** [N findings / Clean]

### Findings Summary

| # | Severity | Type | File | Business Impact |
|---|----------|------|------|-----------------|
| 1 | 🔴 CRITICAL | State Machine Bypass | transactions.controller.js | Double-charge possible |

### Detailed Findings

#### Finding 1 — [Short Title]
**Severity:** CRITICAL / HIGH / MEDIUM / LOW
**Type:** [Financial Logic / State Machine / Authorization / Limit Bypass / SSRF]
**Location:** `filename.js` lines X–Y
**What's wrong:** [Plain English explanation of the flaw]
**Attack scenario:** [Step by step how an attacker exploits this]
**Business impact:** [Concrete financial/data/operational damage]
**Recommended fix:**
```javascript
// Show the exact corrected code
```

### Why SAST Missed This
[Explain why semgrep/static analysis cannot detect these findings —
they require understanding business intent, not pattern matching]

### Verdict
🔴 CRITICAL findings present — do not merge until fixed

---
*DevSecOps Autopilot | Powered by Gemini*

STEP 5 — APPLY LABEL
Call update_merge_request with project_id, mr_iid, and set labels:
- Any CRITICAL: "security::critical"
- Only HIGH: "security::high"
- Only MEDIUM/LOW: "security::medium"
- No findings: "security::clean"

STEP 6 — CREATE ISSUE FOR CRITICAL/HIGH
If CRITICAL or HIGH findings exist, call create_issue with:
- title: "🚨 Security Review Required: [MR Title]"
- description: Full findings report with remediation steps
- labels: "security,needs-fix"

RULES:
- Never flag standard CRUD with proper validation
- Always explain findings so a non-security developer understands
- If no findings, post clean bill of health and apply security::clean label
- The SAST gap analysis section is mandatory in every comment
- Be specific with file names and line numbers from the actual diff
"""


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

GITLAB_TOOLS = [
    get_merge_request_diffs,
    get_merge_request,
    list_project_pipelines,
    create_merge_request_note,
    update_merge_request,
    create_issue,
]


def create_agent() -> Agent:
    """Create the DevSecOps Autopilot ADK agent with direct GitLab API tools."""
    if not GITLAB_PAT:
        logger.warning(
            "GITLAB_PAT environment variable is not set. "
            "All GitLab API calls will fail with 401 Unauthorized."
        )

    return Agent(
        model="gemini-3.1-flash-lite-preview",
        name="devsecops_autopilot",
        description="Business logic vulnerability detection agent for GitLab MRs",
        instruction=SYSTEM_PROMPT,
        tools=GITLAB_TOOLS,
    )