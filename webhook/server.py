import asyncio
import logging
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from agent.agent import create_agent

load_dotenv()

import os

GITLAB_WEBHOOK_SECRET = os.getenv("GITLAB_WEBHOOK_SECRET", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="DevSecOps Autopilot Webhook")

# Initialise ADK runner once at startup — reused across requests
session_service = InMemorySessionService()
runner = Runner(
    agent=create_agent(),
    app_name="devsecops_autopilot",
    session_service=session_service,
)


async def run_agent(message: str) -> str:
    """Run the ADK agent for a given message and return the final text response."""
    session = await session_service.create_session(
        app_name="devsecops_autopilot",
        user_id="gitlab_webhook",
        session_id=str(uuid.uuid4()),
    )
    content = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=message)],
    )
    async for event in runner.run_async(
            user_id="gitlab_webhook",
            session_id=session.id,
            new_message=content,
        ):
            if event.is_final_response():
                logger.info("Agent run finalized execution loop.")
                # Safe extraction of text from the ADK event structure
                if event.content and event.content.parts:
                    return event.content.parts[0].text
                return "Agent completed, but no text was returned."


async def process_mr_event(message: str) -> None:
    """Background task: run the agent, swallow all errors so GitLab never sees a 5xx."""
    try:
        result = await run_agent(message)
        logger.info("Agent finished. Response length: %d chars", len(result))
    except Exception:
        logger.exception("Agent run failed — error suppressed to avoid GitLab retry storm")


@app.post("/webhook")
async def gitlab_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_gitlab_token: str = Header(default=""),
):
    # Validate secret
    if x_gitlab_token != GITLAB_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})

    body = await request.json()

    # Only handle merge_request events
    event_type = body.get("object_kind", "")
    if event_type != "merge_request":
        return {"status": "skipped", "reason": "not a merge_request event"}

    attrs = body.get("object_attributes", {})
    action = attrs.get("action", "")

    if action not in {"open", "update", "reopen"}:
        return {"status": "skipped", "action": action}

    mr_iid = attrs.get("iid")
    mr_title = attrs.get("title", "Untitled MR")
    project = body.get("project", {})
    project_id = project.get("id")
    project_path = project.get("path_with_namespace", str(project_id))
    author = body.get("user", {}).get("username", "unknown")

    message = (
        f"Review merge request !{mr_iid} in project {project_id} "
        f"({project_path}). MR Title: '{mr_title}'. Author: {author}. "
        f"Action: {action}. "
        f"Analyze the diff for business logic vulnerabilities, post your "
        f"findings as a comment on the MR, apply the appropriate security "
        f"label, and create an issue if CRITICAL or HIGH findings exist."
    )

    logger.info("Queuing agent run for MR !%s in project %s", mr_iid, project_id)
    background_tasks.add_task(process_mr_event, message)

    return {"status": "processed"}


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
