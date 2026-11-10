"""Offline tests: no network, no keys. Run with `python -m pytest tests`."""
import importlib
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(ROOT))
PKG = os.path.basename(ROOT)
plugin = importlib.import_module(PKG)
tools = importlib.import_module(PKG + ".tools")
near = importlib.import_module(PKG + ".near")

Y = near.YOCTO


def job(status="Reviewed", subs=None, client="me.testnet", bounty=3 * Y // 10):
    return {"id": 5, "client": client, "description": "d", "bounty": str(bounty), "currency": "Near",
            "status": status, "created_ms": 0, "deadline_ms": 1_800_000_000_000, "reviewed_ms": 0,
            "submissions": subs if subs is not None else [
                {"agent_id": 0, "result_hash": "h", "result_uri": "u", "score": 90, "flagged": False, "review_note": ""},
                {"agent_id": 1, "result_hash": "h", "result_uri": "u", "score": 92, "flagged": False, "review_note": ""},
                {"agent_id": 2, "result_hash": "h", "result_uri": "u", "score": 0, "flagged": True, "review_note": ""},
            ], "intents_ref": None}


class FakeClient:
    def __init__(self, j):
        self.j, self.calls = j, []

    def view(self, contract, method, args=None):
        if method == "get_job":
            return self.j
        if method == "get_jobs":
            return [self.j]
        if method == "get_agents":
            return [{"id": i, "owner": f"o{i}.testnet", "name": f"A{i}", "model": "m", "reputation": 0,
                     "jobs_won": 0, "flags": 0, "inference_credit": str(Y // 10), "active": True} for i in range(3)]
        if method == "get_earnings":
            return "0"
        raise AssertionError(method)

    def call(self, signer, key, contract, method, args, deposit_yocto=0, gas=0):
        self.calls.append((signer, method, args, deposit_yocto))
        return {"tx_hash": "TX", "result": 7}


SECRET = "ed25519:" + near.b58encode(bytes(range(32)) + near.KeyPair(
    "ed25519:" + near.b58encode(bytes(range(32)))).public)


def _setup(j=None, **settings):
    fc = FakeClient(j or job())
    s = {"account_id": "me.testnet", **settings}
    tools.configure(settings=lambda: s, secret=lambda n: SECRET, client_factory=lambda url: fc)
    return fc


def test_payout_matches_contract_rule():
    plan = tools.compute_payout(job())
    shares = [int(r["yocto"]) for r in plan["payouts"]]
    assert sum(shares) == 3 * Y // 10
    assert [r["agent_id"] for r in plan["payouts"]] == [0, 1]
    assert shares[0] == (3 * Y // 10) * 90 // 182
    assert plan["excluded"][0]["flagged"] is True


def test_no_winner_refunds_client():
    j = job(subs=[{"agent_id": 0, "result_hash": "", "result_uri": "", "score": 59, "flagged": False, "review_note": ""}])
    assert tools.compute_payout(j)["refund_to_client"] == "0.3"


def test_amount_parsing_is_exact():
    assert near.near_to_yocto("0.1") == Y // 10
    assert near.near_to_yocto("1") == Y
    for bad in ["", "-1", "abc", "1.2.3", "0." + "1" * 25]:
        with pytest.raises(ValueError):
            near.near_to_yocto(bad)


def test_base58_roundtrip():
    for b in [b"", b"\x00\x00ab", os.urandom(32)]:
        assert near.b58decode(near.b58encode(b)) == b


def test_post_job_sends_deposit():
    fc = _setup()
    out = json.loads(tools.swarm_post_job({"description": "write a haiku", "bounty_near": "0.25"}))
    assert out["ok"] and out["job_id"] == 7
    signer, method, args, dep = fc.calls[0]
    assert (signer, method, dep) == ("me.testnet", "post_job", Y // 4)
    assert args["description"] == "write a haiku"


def test_cap_and_minimum_enforced_in_handler():
    fc = _setup(max_bounty_near="0.5")
    assert not json.loads(tools.swarm_post_job({"description": "x", "bounty_near": "0.6"}))["ok"]
    assert not json.loads(tools.swarm_post_job({"description": "x", "bounty_near": "0.05"}))["ok"]
    assert fc.calls == []


def test_mainnet_off_by_default():
    _setup(network="mainnet")
    out = json.loads(tools.swarm_jobs({}))
    assert not out["ok"] and "allow_mainnet" in out["error"]


def test_approve_only_by_client_and_when_reviewed():
    fc = _setup(job(client="someone.testnet"))
    assert "belongs to" in json.loads(tools.swarm_approve_payout({"job_id": 5}))["error"]
    fc = _setup(job(status="Open"))
    assert not json.loads(tools.swarm_approve_payout({"job_id": 5}))["ok"]
    fc = _setup()
    assert json.loads(tools.swarm_approve_payout({"job_id": 5}))["ok"]
    assert fc.calls[0][1] == "approve" and fc.calls[0][3] == 0


def test_missing_key_fails_cleanly():
    fc = FakeClient(job())
    tools.configure(settings=lambda: {"account_id": "me.testnet"}, secret=lambda n: None, client_factory=lambda u: fc)
    out = json.loads(tools.swarm_approve_payout({"job_id": 5}))
    assert not out["ok"] and "SWARM_NEAR_PRIVATE_KEY" in out["error"]


def test_hook_escalates_writes_and_ignores_reads():
    _setup()
    assert plugin._pre_tool_call("swarm_jobs", {}) is None
    assert plugin._pre_tool_call("terminal", {"command": "ls"}) is None
    d = plugin._pre_tool_call("swarm_post_job", {"description": "x", "bounty_near": "0.2"})
    assert d["action"] == "approve" and "0.2 NEAR" in d["message"]
    assert plugin._pre_tool_call("swarm_approve_payout", {"job_id": 5})["action"] == "approve"
    assert plugin._pre_tool_call("swarm_cancel_job", {"job_id": 5})["action"] == "approve"


def test_hook_blocks_over_cap_before_asking():
    _setup(max_bounty_near="1")
    d = plugin._pre_tool_call("swarm_post_job", {"description": "x", "bounty_near": "2"})
    assert d["action"] == "block"


def test_register_declares_what_it_registers():
    import yaml

    class Ctx:
        def __init__(self):
            self.tools, self.hooks = [], []

        def get_config(self):
            return {}

        def register_tool(self, **kw):
            self.tools.append(kw["name"])

        def register_hook(self, name, fn):
            self.hooks.append(name)

    ctx = Ctx()
    plugin.register(ctx)
    manifest = yaml.safe_load(open(os.path.join(ROOT, "plugin.yaml")))
    assert sorted(ctx.tools) == sorted(manifest["provides_tools"])
    assert ctx.hooks == manifest["provides_hooks"]


def test_keypair_rejects_mismatched_public_half():
    bad = "ed25519:" + near.b58encode(bytes(range(32)) + b"\x01" * 32)
    with pytest.raises(ValueError):
        near.KeyPair(bad)
