"""Tool handlers. Each returns a JSON string and never raises."""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from . import near

PASS_SCORE = 60
MIN_BOUNTY_YOCTO = 10**23  # 0.1 NEAR, the contract minimum
ACCOUNT_RE = re.compile(r"^(?=.{2,64}$)([a-z\d]+[-_])*[a-z\d]+(\.([a-z\d]+[-_])*[a-z\d]+)*$")

DEFAULTS = {
    "network": "testnet",
    "contract_id": "yunganas.testnet",
    "account_id": "",
    "rpc_url": "",
    "max_bounty_near": "1",
    "allow_mainnet": False,
}

# Wired by register(): returns this plugin's settings dict, and a secret getter.
_settings: Callable[[], dict] = lambda: {}
_secret: Callable[[str], str | None] = lambda name: None
_client_factory: Callable[[str], Any] = lambda url: near.NearClient(url)


def configure(settings: Callable[[], dict], secret: Callable[[str], str | None], client_factory=None) -> None:
    global _settings, _secret, _client_factory
    _settings, _secret = settings, secret
    if client_factory is not None:
        _client_factory = client_factory


def _cfg() -> dict:
    c = dict(DEFAULTS)
    c.update({k: v for k, v in (_settings() or {}).items() if v not in (None, "")})
    return c


def _ok(**data) -> str:
    return json.dumps({"ok": True, **data})


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg})


def _client(c: dict):
    if c["network"] not in near.DEFAULT_RPC:
        raise ValueError("network must be testnet or mainnet")
    if c["network"] == "mainnet" and not c.get("allow_mainnet"):
        raise PermissionError("mainnet is off; set allow_mainnet: true in this plugin's settings to use real NEAR")
    return _client_factory(c["rpc_url"] or near.DEFAULT_RPC[c["network"]])


def _fmt_job(j: dict, full: bool = False) -> dict:
    out = {
        "id": j["id"], "status": j["status"], "client": j["client"],
        "bounty_near": near.yocto_to_near(j["bounty"]), "currency": j["currency"],
        "deadline_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(j["deadline_ms"] / 1000)),
        "submissions": len(j["submissions"]),
        "description": j["description"] if full else j["description"][:160],
    }
    if full:
        out["submissions"] = [
            {"agent_id": s["agent_id"], "score": s["score"], "flagged": s["flagged"],
             "review_note": s["review_note"], "result_uri": s["result_uri"]}
            for s in j["submissions"]
        ]
    return out


def compute_payout(job: dict) -> dict:
    """Mirror of the contract's settle(): pro rata by score over unflagged >=60 submissions."""
    bounty = int(job["bounty"])
    winners = [(s["agent_id"], int(s["score"] or 0)) for s in job["submissions"]
               if not s["flagged"] and (s["score"] or 0) >= PASS_SCORE]
    total = sum(w[1] for w in winners)
    if total == 0:
        return {"refund_to_client": near.yocto_to_near(bounty), "payouts": []}
    paid, rows = 0, []
    for i, (aid, score) in enumerate(winners):
        share = bounty - paid if i == len(winners) - 1 else bounty * score // total
        paid += share
        rows.append({"agent_id": aid, "score": score, "near": near.yocto_to_near(share), "yocto": str(share)})
    excluded = [{"agent_id": s["agent_id"], "score": s["score"], "flagged": s["flagged"]}
                for s in job["submissions"] if s["agent_id"] not in {w[0] for w in winners}]
    return {"payouts": rows, "excluded": excluded}


def _signer(c: dict):
    acct = (c.get("account_id") or "").strip()
    if not ACCOUNT_RE.match(acct):
        raise ValueError("set account_id in this plugin's settings to the NEAR account that signs")
    secret = _secret("SWARM_NEAR_PRIVATE_KEY")
    if not secret:
        raise PermissionError("SWARM_NEAR_PRIVATE_KEY is not set; add it in Hermes (hermes config set SWARM_NEAR_PRIVATE_KEY ...)")
    return acct, near.KeyPair(secret.strip())


# ---------------- read-only ----------------

def swarm_jobs(args: dict, **kwargs) -> str:
    try:
        c = _cfg()
        limit = max(1, min(int(args.get("limit", 20)), 50))
        jobs = _client(c).view(c["contract_id"], "get_jobs", {"from": 0, "limit": 50})
        if args.get("status"):
            jobs = [j for j in jobs if j["status"] == args["status"]]
        jobs = sorted(jobs, key=lambda j: j["id"], reverse=True)[:limit]
        return _ok(network=c["network"], contract=c["contract_id"], jobs=[_fmt_job(j) for j in jobs])
    except Exception as e:  # noqa: BLE001
        return _err(f"swarm_jobs failed: {e}")


def swarm_job(args: dict, **kwargs) -> str:
    try:
        c = _cfg()
        j = _client(c).view(c["contract_id"], "get_job", {"job_id": int(args["job_id"])})
        if not j:
            return _err(f"no job {args['job_id']}")
        return _ok(job=_fmt_job(j, full=True))
    except Exception as e:  # noqa: BLE001
        return _err(f"swarm_job failed: {e}")


def swarm_agents(args: dict, **kwargs) -> str:
    try:
        c = _cfg()
        limit = max(1, min(int(args.get("limit", 20)), 50))
        agents = _client(c).view(c["contract_id"], "get_agents", {"from": 0, "limit": limit})
        return _ok(agents=[{
            "id": a["id"], "name": a["name"], "owner": a["owner"], "model": a["model"],
            "reputation": a["reputation"], "jobs_won": a["jobs_won"], "flags": a["flags"],
            "inference_credit_near": near.yocto_to_near(a["inference_credit"]), "active": a["active"],
        } for a in agents])
    except Exception as e:  # noqa: BLE001
        return _err(f"swarm_agents failed: {e}")


def swarm_payout_preview(args: dict, **kwargs) -> str:
    try:
        c = _cfg()
        cl = _client(c)
        j = cl.view(c["contract_id"], "get_job", {"job_id": int(args["job_id"])})
        if not j:
            return _err(f"no job {args['job_id']}")
        if j["status"] not in ("Reviewed", "Disputed"):
            return _err(f"job {j['id']} is {j['status']}; only a Reviewed job can be approved")
        plan = compute_payout(j)
        owners = {}
        if plan["payouts"]:
            for a in cl.view(c["contract_id"], "get_agents", {"from": 0, "limit": 50}):
                owners[a["id"]] = (a["owner"], a["name"])
        for row in plan["payouts"]:
            row["owner"], row["agent_name"] = owners.get(row["agent_id"], ("?", "?"))
            row.pop("yocto", None)
        return _ok(job_id=j["id"], bounty_near=near.yocto_to_near(j["bounty"]), client=j["client"], **plan)
    except Exception as e:  # noqa: BLE001
        return _err(f"swarm_payout_preview failed: {e}")


def swarm_earnings(args: dict, **kwargs) -> str:
    try:
        c = _cfg()
        acct = (args.get("account_id") or c.get("account_id") or "").strip()
        if not ACCOUNT_RE.match(acct):
            return _err("give account_id or set it in this plugin's settings")
        cl = _client(c)
        out = {cur: near.yocto_to_near(cl.view(c["contract_id"], "get_earnings", {"account_id": acct, "currency": cur}))
               for cur in ("Near", "WNear")}
        return _ok(account_id=acct, withdrawable=out)
    except Exception as e:  # noqa: BLE001
        return _err(f"swarm_earnings failed: {e}")


# ---------------- writes (gated by the pre_tool_call approval hook) ----------------

def check_post(args: dict, c: dict | None = None) -> int:
    """Validate a post_job request; returns the bounty in yocto or raises."""
    c = c or _cfg()
    bounty = near.near_to_yocto(args.get("bounty_near", ""))
    cap = near.near_to_yocto(c["max_bounty_near"])
    if bounty < MIN_BOUNTY_YOCTO:
        raise ValueError("bounty must be at least 0.1 NEAR (contract minimum)")
    if bounty > cap:
        raise PermissionError(f"bounty {near.yocto_to_near(bounty)} NEAR is above this plugin's cap of "
                              f"{near.yocto_to_near(cap)} NEAR (max_bounty_near setting)")
    desc = (args.get("description") or "").strip()
    if not desc or len(desc.encode()) > 2000:
        raise ValueError("description must be 1-2000 bytes")
    return bounty


def swarm_post_job(args: dict, **kwargs) -> str:
    try:
        c = _cfg()
        bounty = check_post(args, c)
        hours = float(args.get("deadline_hours", 24))
        if not 1 <= hours <= 720:
            return _err("deadline_hours must be between 1 and 720")
        acct, key = _signer(c)
        deadline_ms = int(time.time() * 1000 + hours * 3_600_000)
        res = _client(c).call(acct, key, c["contract_id"], "post_job",
                              {"description": args["description"].strip(), "deadline_ms": deadline_ms},
                              deposit_yocto=bounty)
        return _ok(job_id=res["result"], tx_hash=res["tx_hash"], bounty_near=near.yocto_to_near(bounty),
                   network=c["network"])
    except Exception as e:  # noqa: BLE001
        return _err(f"swarm_post_job failed: {e}")


def _client_only_call(method: str, args: dict, need_status: tuple[str, ...]) -> str:
    c = _cfg()
    acct, key = _signer(c)
    cl = _client(c)
    job_id = int(args["job_id"])
    j = cl.view(c["contract_id"], "get_job", {"job_id": job_id})
    if not j:
        return _err(f"no job {job_id}")
    if j["client"] != acct:
        return _err(f"job {job_id} belongs to {j['client']}, not {acct}")
    if j["status"] not in need_status:
        return _err(f"job {job_id} is {j['status']}; {method} needs {' or '.join(need_status)}")
    res = cl.call(acct, key, c["contract_id"], method, {"job_id": job_id})
    return _ok(job_id=job_id, tx_hash=res["tx_hash"], network=c["network"])


def swarm_approve_payout(args: dict, **kwargs) -> str:
    try:
        return _client_only_call("approve", args, ("Reviewed",))
    except Exception as e:  # noqa: BLE001
        return _err(f"swarm_approve_payout failed: {e}")


def swarm_cancel_job(args: dict, **kwargs) -> str:
    try:
        return _client_only_call("cancel_job", args, ("Open", "AwaitingFunds"))
    except Exception as e:  # noqa: BLE001
        return _err(f"swarm_cancel_job failed: {e}")


WRITE_TOOLS = {"swarm_post_job", "swarm_approve_payout", "swarm_cancel_job"}


def approval_message(tool_name: str, args: dict) -> str:
    c = _cfg()
    where = f"{c['network']} contract {c['contract_id']} as {c.get('account_id') or '(account not set)'}"
    if tool_name == "swarm_post_job":
        return (f"Swarm-for-Hire: lock {args.get('bounty_near')} NEAR as a bounty for job "
                f"\"{str(args.get('description', ''))[:120]}\" on {where}.")
    if tool_name == "swarm_approve_payout":
        return f"Swarm-for-Hire: release the escrowed bounty of job {args.get('job_id')} to the winning agents on {where}."
    return f"Swarm-for-Hire: cancel job {args.get('job_id')} and refund its bounty on {where}."
