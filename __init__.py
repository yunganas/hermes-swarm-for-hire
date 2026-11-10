"""Swarm-for-Hire: post, inspect and pay AI-agent jobs on a NEAR escrow from Hermes.

Every tool that moves money is escalated to Hermes's human-approval gate by a
pre_tool_call hook, and bounties above a configurable cap are refused outright.
"""
import logging

from . import schemas, tools

logger = logging.getLogger(__name__)

_HANDLERS = {
    "swarm_jobs": tools.swarm_jobs,
    "swarm_job": tools.swarm_job,
    "swarm_agents": tools.swarm_agents,
    "swarm_payout_preview": tools.swarm_payout_preview,
    "swarm_earnings": tools.swarm_earnings,
    "swarm_post_job": tools.swarm_post_job,
    "swarm_approve_payout": tools.swarm_approve_payout,
    "swarm_cancel_job": tools.swarm_cancel_job,
}

_EMOJI = {"swarm_post_job": "🐝", "swarm_approve_payout": "💸", "swarm_cancel_job": "↩️"}


def _get_secret(name):
    try:
        from agent.secret_scope import get_secret
    except Exception:  # noqa: BLE001  (outside Hermes, e.g. unit tests)
        return None
    return get_secret(name)


def _pre_tool_call(tool_name=None, args=None, task_id=None, **kwargs):
    """Escalate every money-moving Swarm tool to the human-approval gate.

    Requests that would be refused anyway (over the cap, below the minimum) are
    blocked here so the user is never asked to approve something that cannot run.
    """
    if tool_name not in tools.WRITE_TOOLS:
        return None
    args = args or {}
    try:
        if tool_name == "swarm_post_job":
            tools.check_post(args)
    except Exception as e:  # noqa: BLE001
        return {"action": "block", "message": f"Swarm-for-Hire refused: {e}"}
    try:
        msg = tools.approval_message(tool_name, args)
    except Exception:  # noqa: BLE001
        msg = f"Swarm-for-Hire: {tool_name} moves NEAR on-chain."
    return {"action": "approve", "message": msg}


def register(ctx):
    tools.configure(settings=lambda: ctx.get_config() or {}, secret=_get_secret)
    for schema in schemas.ALL:
        name = schema["name"]
        ctx.register_tool(
            name=name,
            toolset="swarm_for_hire",
            schema=schema,
            handler=_HANDLERS[name],
            description=schema["description"].split(".")[0],
            emoji=_EMOJI.get(name, "🔎"),
        )
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    logger.debug("swarm-for-hire: registered %d tools", len(schemas.ALL))
