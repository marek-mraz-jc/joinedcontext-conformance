"""The visitor token every question to a public assistant carries (T-4653, API/05 §1.2.1), earned
the way the widget earns it: a challenge, the first nonce whose SHA-256 of `{challenge}:{nonce}`
starts with the challenge's zero bits, and the token that proof buys. A token is good for an hour;
`token()` keeps one per assistant and earns a new one a minute before it ends."""

import hashlib
import json
import time
import urllib.request

_held: dict[tuple[str, str], tuple[str, float]] = {}


def zero_bits(digest: bytes) -> int:
    bits = 0
    for byte in digest:
        if byte:
            return bits + 8 - byte.bit_length()
        bits += 8
    return bits


def solve(challenge: str, difficulty: int) -> str:
    nonce = 0
    while zero_bits(hashlib.sha256(f"{challenge}:{nonce}".encode()).digest()) < difficulty:
        nonce += 1
    return str(nonce)


def _call(url: str, origin: str | None, body: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if origin:
        headers["Origin"] = origin
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def earn(base: str, public_id: str, origin: str | None = None) -> str:
    """A new token of `public_id` on the assistant at `base`."""
    root = f"{base.rstrip('/')}/api/v1/d/{public_id}"
    challenge = _call(f"{root}/challenge", origin)
    nonce = solve(challenge["challenge"], int(challenge["difficulty"]))
    return _call(f"{root}/token", origin, {"challenge": challenge["challenge"], "nonce": nonce})["token"]


def token(base: str, public_id: str, origin: str | None = None) -> str:
    """The token held for `public_id`, earned again a minute before its hour ends."""
    key = (base, public_id)
    held = _held.get(key)
    if held is None or time.monotonic() > held[1]:
        held = (earn(base, public_id, origin), time.monotonic() + 3_540)
        _held[key] = held
    return held[0]


if __name__ == "__main__":
    # The proof's own check: no network.
    assert zero_bits(bytes([0, 0x0F, 0xFF])) == 12
    assert zero_bits(bytes([0x80])) == 0
    assert zero_bits(bytes(2)) == 16
    found = solve("3a1f.1760792400.10.9f2c", 10)
    assert zero_bits(hashlib.sha256(f"3a1f.1760792400.10.9f2c:{found}".encode()).digest()) >= 10
    print("visitor: ok")
