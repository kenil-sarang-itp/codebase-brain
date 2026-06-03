"""
Webhook route — `/webhook`.

Receives GitHub webhook events. When a pull request is *merged*, it triggers
impact-based documentation regeneration (spec PR-merge flow).

Security: GitHub signs every webhook with an HMAC-SHA256 of the raw body using
the shared secret. This handler verifies that signature before trusting the
payload — an unsigned or wrongly-signed request is rejected with 401. The raw
body must be read *before* JSON parsing, because the signature is computed over
exact bytes.

Like `/index`, the actual regeneration is enqueued onto RQ so the webhook
returns to GitHub fast (GitHub expects a prompt 2xx).
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import APIRouter, Header, Request

from app.config.settings import get_settings
from app.core.exceptions import WebhookVerificationError
from app.core.logging import get_logger
from app.schemas.api_schemas import WebhookResponse
from app.workers.queue import enqueue_pr_sync

logger = get_logger(__name__)

router = APIRouter(tags=["webhook"])


def _verify_signature(raw_body: bytes, signature_header: str | None) -> None:
    """Verify GitHub's `X-Hub-Signature-256` HMAC over the raw request body.

    Raises:
        WebhookVerificationError: If the signature is missing or invalid.
    """
    secret = get_settings().github_webhook_secret
    if not signature_header:
        raise WebhookVerificationError("Missing webhook signature header.")

    # Header form is "sha256=<hexdigest>".
    expected = (
        "sha256="
        + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    )
    # Constant-time compare to avoid timing attacks.
    if not hmac.compare_digest(expected, signature_header):
        raise WebhookVerificationError("Webhook signature verification failed.")


@router.post("/webhook", response_model=WebhookResponse)
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> WebhookResponse:
    """Handle a GitHub webhook event.

    Only merged-pull-request events trigger work; everything else is
    acknowledged and ignored. The signature is verified first, before the
    payload is trusted.
    """
    # Read the raw body BEFORE parsing — the signature covers exact bytes.
    raw_body = await request.body()
    _verify_signature(raw_body, x_hub_signature_256)

    # Now it is safe to parse.
    payload = await request.json()

    # We act only on pull_request events where the PR was actually merged.
    if x_github_event != "pull_request":
        return WebhookResponse(
            received=True,
            action="ignored",
            detail=f"Event '{x_github_event}' is not handled.",
        )

    action = payload.get("action", "")
    pr = payload.get("pull_request", {}) or {}
    merged = bool(pr.get("merged", False))

    if action != "closed" or not merged:
        return WebhookResponse(
            received=True,
            action="ignored",
            detail="Pull request was not merged; nothing to regenerate.",
        )

    pr_number = str(pr.get("number", ""))
    repo_full_name = (payload.get("repository", {}) or {}).get("full_name", "")

    # Enqueue impact-based regeneration on the job queue.
    enqueue_pr_sync(pr_number=pr_number, repo=repo_full_name)

    logger.info("Queued PR-sync for merged PR #%s in %s", pr_number, repo_full_name)
    return WebhookResponse(
        received=True,
        action="pr_sync_queued",
        detail=f"Doc regeneration queued for merged PR #{pr_number}.",
    )
