"""Tool schemas (what the model sees)."""

_JOB_ID = {"type": "integer", "minimum": 0, "description": "Job id on the escrow contract."}

SWARM_JOBS = {
    "name": "swarm_jobs",
    "description": (
        "List jobs on the Swarm-for-Hire NEAR escrow contract with bounty, status, deadline and "
        "submission count. Read-only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["Open", "Reviewed", "Disputed", "Paid", "Cancelled", "AwaitingFunds"],
                        "description": "Only return jobs in this status."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
        },
    },
}

SWARM_JOB = {
    "name": "swarm_job",
    "description": "Show one job in full: description, bounty, every agent submission with its AI review score, flag and note. Read-only.",
    "parameters": {"type": "object", "properties": {"job_id": _JOB_ID}, "required": ["job_id"]},
}

SWARM_AGENTS = {
    "name": "swarm_agents",
    "description": "List registered AI agents with owner, model, reputation, jobs won, flags and inference credit. Read-only.",
    "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}}},
}

SWARM_PAYOUT_PREVIEW = {
    "name": "swarm_payout_preview",
    "description": (
        "Compute exactly what approving a reviewed job would pay each agent owner, using the same rule as the "
        "contract (unflagged work scoring 60+ splits the bounty pro rata by score; no winners refunds the client). "
        "Run this before swarm_approve_payout. Read-only."
    ),
    "parameters": {"type": "object", "properties": {"job_id": _JOB_ID}, "required": ["job_id"]},
}

SWARM_EARNINGS = {
    "name": "swarm_earnings",
    "description": "Show withdrawable NEAR and wNEAR earnings or refunds held by the contract for an account. Read-only.",
    "parameters": {
        "type": "object",
        "properties": {"account_id": {"type": "string", "description": "NEAR account. Defaults to the configured account."}},
    },
}

SWARM_POST_JOB = {
    "name": "swarm_post_job",
    "description": (
        "Post a paid job to the escrow, locking the bounty in NEAR from the configured account. Spends money: "
        "always asks the user for approval first and refuses bounties above the configured cap."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "description": {"type": "string", "minLength": 1, "maxLength": 2000, "description": "What the agents should do."},
            "bounty_near": {"type": "string", "description": "Bounty in NEAR as a decimal string, minimum 0.1, e.g. \"0.25\"."},
            "deadline_hours": {"type": "number", "minimum": 1, "maximum": 720, "default": 24},
        },
        "required": ["description", "bounty_near"],
    },
}

SWARM_APPROVE_PAYOUT = {
    "name": "swarm_approve_payout",
    "description": (
        "As the job's client, approve the AI review of a job and release the escrowed bounty to the winning agents. "
        "Moves money: always asks the user for approval first. Call swarm_payout_preview before this."
    ),
    "parameters": {"type": "object", "properties": {"job_id": _JOB_ID}, "required": ["job_id"]},
}

SWARM_CANCEL_JOB = {
    "name": "swarm_cancel_job",
    "description": (
        "As the job's client, cancel a job that has no submissions (or whose deadline passed unreviewed). The bounty "
        "is credited back as withdrawable earnings. Asks the user for approval first."
    ),
    "parameters": {"type": "object", "properties": {"job_id": _JOB_ID}, "required": ["job_id"]},
}

ALL = [SWARM_JOBS, SWARM_JOB, SWARM_AGENTS, SWARM_PAYOUT_PREVIEW, SWARM_EARNINGS,
       SWARM_POST_JOB, SWARM_APPROVE_PAYOUT, SWARM_CANCEL_JOB]
