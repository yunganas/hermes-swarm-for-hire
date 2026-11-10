"""Minimal NEAR JSON-RPC client: view calls and signed FunctionCall transactions.

Pure Python plus `cryptography` and `httpx` (both already in Hermes core).
No keys are read from disk: the caller passes the private key string it got
from Hermes's secret route.
"""
from __future__ import annotations

import base64
import hashlib
import json
import struct
from typing import Any

import httpx

YOCTO = 10**24
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
DEFAULT_RPC = {"testnet": "https://rpc.testnet.near.org", "mainnet": "https://rpc.mainnet.near.org"}


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        i = _B58.find(ch)
        if i < 0:
            raise ValueError("invalid base58 character")
        n = n * 58 + i
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + raw


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    pad = len(b) - len(b.lstrip(b"\x00"))
    return "1" * pad + out


def near_to_yocto(amount: str | float) -> int:
    """Parse a decimal NEAR amount exactly (no float rounding)."""
    s = str(amount).strip()
    if not s or s.startswith("-"):
        raise ValueError("amount must be a positive number")
    whole, _, frac = s.partition(".")
    if not (whole or frac) or not (whole or "0").isdigit() or (frac and not frac.isdigit()):
        raise ValueError(f"bad NEAR amount: {amount!r}")
    if len(frac) > 24:
        raise ValueError("too many decimals")
    return int(whole or "0") * YOCTO + int((frac or "0").ljust(24, "0"))


def yocto_to_near(y: int | str) -> str:
    y = int(y)
    whole, frac = divmod(y, YOCTO)
    f = str(frac).rjust(24, "0").rstrip("0")[:6]
    return f"{whole}.{f}" if f else str(whole)


# ---------------- borsh ----------------

def _u8(v: int) -> bytes:
    return struct.pack("<B", v)


def _u32(v: int) -> bytes:
    return struct.pack("<I", v)


def _u64(v: int) -> bytes:
    return struct.pack("<Q", v)


def _u128(v: int) -> bytes:
    return v.to_bytes(16, "little")


def _str(s: str) -> bytes:
    b = s.encode()
    return _u32(len(b)) + b


def _bytes(b: bytes) -> bytes:
    return _u32(len(b)) + b


def serialize_function_call_tx(
    signer_id: str,
    public_key: bytes,
    nonce: int,
    receiver_id: str,
    block_hash: bytes,
    method: str,
    args: dict,
    gas: int,
    deposit: int,
) -> bytes:
    action = _u8(2) + _str(method) + _bytes(json.dumps(args, separators=(",", ":")).encode()) + _u64(gas) + _u128(deposit)
    return (
        _str(signer_id)
        + _u8(0) + public_key
        + _u64(nonce)
        + _str(receiver_id)
        + block_hash
        + _u32(1) + action
    )


class KeyPair:
    def __init__(self, secret: str):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        if not secret.startswith("ed25519:"):
            raise ValueError("private key must look like ed25519:<base58>")
        raw = b58decode(secret.split(":", 1)[1])
        if len(raw) not in (32, 64):
            raise ValueError("unexpected ed25519 key length")
        self._sk = Ed25519PrivateKey.from_private_bytes(raw[:32])
        from cryptography.hazmat.primitives import serialization

        self.public = self._sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        if len(raw) == 64 and raw[32:] != self.public:
            raise ValueError("private key and embedded public key do not match")

    @property
    def public_str(self) -> str:
        return "ed25519:" + b58encode(self.public)

    def sign(self, msg: bytes) -> bytes:
        return self._sk.sign(msg)


class NearClient:
    def __init__(self, rpc_url: str, timeout: float = 30.0, transport: Any = None):
        self.rpc_url = rpc_url
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def _rpc(self, method: str, params: Any) -> Any:
        r = self._http.post(self.rpc_url, json={"jsonrpc": "2.0", "id": "hermes", "method": method, "params": params})
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            err = body["error"]
            raise RuntimeError(json.dumps(err.get("data") or err.get("cause") or err)[:500])
        return body["result"]

    def view(self, contract: str, method: str, args: dict | None = None) -> Any:
        res = self._rpc("query", {
            "request_type": "call_function",
            "finality": "final",
            "account_id": contract,
            "method_name": method,
            "args_base64": base64.b64encode(json.dumps(args or {}).encode()).decode(),
        })
        if "error" in res:
            raise RuntimeError(str(res["error"])[:500])
        return json.loads(bytes(res["result"]).decode() or "null")

    def call(self, signer_id: str, key: KeyPair, contract: str, method: str, args: dict,
             deposit_yocto: int = 0, gas: int = 100 * 10**12) -> dict:
        ak = self._rpc("query", {"request_type": "view_access_key", "finality": "final",
                                 "account_id": signer_id, "public_key": key.public_str})
        if "error" in ak:
            raise RuntimeError(f"access key not found for {signer_id}")
        if ak.get("permission") != "FullAccess" and deposit_yocto > 0:
            raise RuntimeError("a function-call access key cannot attach a deposit; use a full-access key")
        nonce = int(ak["nonce"]) + 1
        block_hash = b58decode(ak["block_hash"])
        tx = serialize_function_call_tx(signer_id, key.public, nonce, contract, block_hash,
                                        method, args, gas, deposit_yocto)
        sig = key.sign(hashlib.sha256(tx).digest())
        signed = tx + _u8(0) + sig
        out = self._rpc("broadcast_tx_commit", [base64.b64encode(signed).decode()])
        status = out.get("status", {})
        if "Failure" in status:
            raise RuntimeError("transaction failed: " + json.dumps(status["Failure"])[:400])
        val = status.get("SuccessValue")
        decoded = json.loads(base64.b64decode(val).decode()) if val else None
        return {"tx_hash": out["transaction"]["hash"], "result": decoded}
