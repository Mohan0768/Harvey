"""Send one approved email, now, exactly once.

The background Sender drains the outbox in batches for autopilot. This is
the other path: a person clicked Approve on one specific email and is
waiting for an answer, so it is synchronous and reports the provider's real
response rather than queueing and hoping.

Three rules it exists to enforce:

  * Exactly once. state.claim_outbox_for_send is an atomic status flip, so
    a double-click, a page refresh or a second tab cannot send twice.
  * Never "sent" on a maybe. The row becomes 'sent' only after the provider
    returns ok, and only then does it carry a message id.
  * A failure stays retryable, with the provider's own words kept on the
    row so the person can see what actually went wrong.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from harvey.gate import pre_send_check
from harvey.importer import EMAIL_RE
from harvey.integrations.mail_provider import get_mail_provider

logger = logging.getLogger("harvey.sending")


@dataclass
class SendOutcome:
    ok: bool
    status: str = ""          # the row's status after this call
    message: str = ""         # plain words for the person who clicked
    message_id: str = ""      # provider message id, only when ok
    thread_ref: str = ""      # provider thread id, only when ok
    already: bool = False     # the row was not claimable


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


async def _fail(state, item_id: str, reason: str) -> SendOutcome:
    """Park a claimed row back in a retryable state, with the real reason."""
    await state.update_outbox_item(item_id, status="failed", error=reason[:300])
    await state.log_action(
        action_type="email_send_failed", agent="dashboard",
        details={"outbox_id": item_id, "error": reason[:300]})
    logger.warning("send failed for %s: %s", item_id, reason)
    return SendOutcome(False, "failed", reason)


async def send_outbox_item(state, config, env, item_id: str) -> SendOutcome:
    """Approve-and-send one outbox row. Returns what to tell the person."""
    item = await state.claim_outbox_for_send(item_id)
    if item is None:
        # Not claimable. Either it is gone, already sent, or in flight.
        current = await state.get_outbox_item(item_id)
        if current is None:
            return SendOutcome(False, "", "That email is no longer in the outbox.",
                               already=True)
        status = (current.get("status") or "").strip()
        if status == "sent":
            return SendOutcome(
                True, "sent",
                "Already sent to " + (current.get("to_email") or "") + ".",
                message_id=current.get("message_id") or "",
                thread_ref=current.get("thread_ref") or "", already=True)
        if status == "sending":
            return SendOutcome(False, status,
                               "That email is being sent right now.", already=True)
        return SendOutcome(
            False, status,
            "Not sent: this email is " + (status or "in an unknown state") + ".",
            already=True)

    to_email = (item.get("to_email") or "").strip()
    subject = item.get("subject") or ""
    body = item.get("body") or ""
    try:
        if not EMAIL_RE.match(to_email.lower()):
            return await _fail(state, item_id,
                               (to_email or "An empty address") +
                               " is not a valid email address.")

        prospect = None
        if item.get("prospect_id"):
            prospect = await state.get_prospect(item["prospect_id"])

        gate = pre_send_check(to_email, subject, body, prospect=prospect,
                              allow_risky=True, kind=item.get("kind") or "manual")
        if not gate.ok:
            return await _fail(state, item_id,
                               "The pre-send gate blocked it: " +
                               "; ".join(gate.reasons))

        provider = get_mail_provider(config, env)
        if provider is None:
            return await _fail(
                state, item_id,
                "No sending provider is configured. Set channels.email.provider "
                "to gmail in your config.")
        if not provider.is_configured():
            # A provider that can name the missing step says so; the others
            # fall back to the generic line.
            detail = provider.readiness() if hasattr(provider, "readiness") else ""
            return await _fail(
                state, item_id,
                detail or (provider.name.capitalize() + " is not connected "
                           "yet. Connect it under Settings, Email "
                           "Integration, then approve again."))

        result = await provider.send_email(
            to_email, subject, body,
            thread_ref=item.get("thread_ref") or "",
            in_reply_to=item.get("in_reply_to") or "")
        if not result.ok:
            # The provider's own words, not a paraphrase: rate limits and
            # expired tokens read very differently and the person needs to
            # know which one they hit.
            return await _fail(state, item_id,
                               result.error or "the provider rejected it")
    except Exception as exc:  # a crash must not strand the row in 'sending'
        return await _fail(state, item_id, type(exc).__name__ + ": " + str(exc))

    sent_at = _now()
    await state.update_outbox_item(
        item_id, status="sent", sent_at=sent_at, error="",
        message_id=result.message_id or "", thread_ref=result.thread_ref or "")

    if prospect is not None and prospect.status in ("new", "queued"):
        await state.update_prospect_status(prospect.id, "contacted")

    await _record_conversation(state, item, result, subject, body, sent_at)

    await state.log_action(
        action_type="email_sent", agent="dashboard",
        details={"outbox_id": item_id, "to": to_email,
                 "provider": provider.name, "kind": item.get("kind") or "",
                 "message_id": result.message_id or "",
                 "thread_ref": result.thread_ref or ""})
    logger.info("sent %s to %s via %s", item_id, to_email, provider.name)

    return SendOutcome(True, "sent", "Sent to " + to_email + ".",
                       message_id=result.message_id or "",
                       thread_ref=result.thread_ref or "")


async def _record_conversation(state, item, result, subject, body, sent_at):
    """Put the sent message in a conversation, keyed by the provider thread.

    Replies arrive on the same thread id, so grouping on it here is what
    lets Conversations show the exchange rather than two loose messages.
    Best effort: a conversation that cannot be written must never turn a
    delivered email back into a failure.
    """
    try:
        from harvey.models.conversation import Conversation, Message

        convo = None
        if item.get("conversation_id"):
            convo = await state.get_conversation(item["conversation_id"])
        if convo is None and result.thread_ref:
            convo = await state.get_conversation_by_thread(result.thread_ref)

        message = Message(sender="harvey", content=body)
        if convo is None:
            convo = Conversation(
                id="", prospect_id=item.get("prospect_id") or "",
                campaign_id=item.get("campaign_id") or "",
                channel="email", thread=[message])
            convo_id = await state.add_conversation(convo)
            # title is a column, not a model field, so it is set after insert.
            await state.update_conversation(convo_id, title=subject[:120])
        else:
            convo.thread.append(message)
            await state.update_conversation(
                convo.id, thread_json=convo.thread_json())
            convo_id = convo.id

        await state.update_outbox_item(item["id"], conversation_id=convo_id)
    except Exception as exc:
        logger.debug("conversation record skipped: %s", exc)
