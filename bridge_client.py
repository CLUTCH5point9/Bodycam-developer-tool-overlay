"""Minimal client for the ClaudeBridge file-RPC protocol (same protocol as bridge.py).

Talks to the ClaudeBridge UE4SS mod over two files under %LOCALAPPDATA%\\Temp\\<name>_bridge.
Both sides write to a temp name and rename, so neither ever reads a half-written file.
"""
import os
import time

BRIDGE_NAME = "bodycam"
_DIR = os.path.join(os.environ["LOCALAPPDATA"], "Temp", f"{BRIDGE_NAME}_bridge")
_REQ = os.path.join(_DIR, "req.txt")
_RESP = os.path.join(_DIR, "resp.txt")
_TMP = os.path.join(_DIR, "req.tmp")
_SEQ = os.path.join(_DIR, "seq.txt")


class BridgeError(Exception):
    pass


class BridgeTimeout(BridgeError):
    pass


_RETURN_MARKER = "-- return: "


def _extract_return_value(body):
    """ClaudeBridge appends '-- return: <value>' after any print() output when the
    Lua payload ends with `return <x>`. Our payloads only care about the returned
    value, so pull just that part out (discarding any print() lines before it)."""
    idx = body.find(_RETURN_MARKER)
    if idx == -1:
        return body
    return body[idx + len(_RETURN_MARKER):]


def _next_id():
    n = 0
    if os.path.exists(_SEQ):
        try:
            n = int(open(_SEQ).read().strip())
        except ValueError:
            n = 0
    n += 1
    with open(_SEQ, "w") as f:
        f.write(str(n))
    return n


def _send(src, timeout):
    """Sends src, waits for the matching response. Returns raw body (str).
    Raises BridgeTimeout if the game/mod doesn't answer, BridgeError on a Lua error."""
    os.makedirs(_DIR, exist_ok=True)
    rid = _next_id()

    if os.path.exists(_RESP):
        try:
            os.remove(_RESP)
        except OSError:
            pass

    with open(_TMP, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{rid}\n{src}")
    os.replace(_TMP, _REQ)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(_RESP):
            try:
                data = open(_RESP, encoding="utf-8", errors="replace").read()
            except OSError:
                time.sleep(0.05)
                continue
            lines = data.split("\n")
            if len(lines) >= 2 and lines[0].strip() == str(rid):
                status = lines[1].strip()
                body = "\n".join(lines[2:])
                if status != "OK":
                    raise BridgeError(body.strip() or f"bridge returned status {status}")
                return body
        time.sleep(0.08)
    raise BridgeTimeout(
        f"No response from the game within {timeout}s. Check: (1) Bodycam is running, "
        "(2) it's the same install this overlay was set up for, (3) the ClaudeBridge mod "
        "is listed in ue4ss/Mods/mods.txt. If ClaudeBridge was never installed, run "
        "install_bridge.py once, then fully restart the game."
    )


def run_lua(src, timeout=15.0):
    """Run a Lua payload, returning just the `return`d value (print() output before
    the '-- return: ' marker, if any, is discarded). This is what all the structured
    game_api functions use."""
    return _extract_return_value(_send(src, timeout))


def run_lua_raw(src, timeout=15.0):
    """Run a Lua payload, returning the FULL response body untouched -- both any
    print() output AND the trailing '-- return: <value>' marker, if present. Meant
    for an interactive console where seeing everything matters more than a clean
    single value."""
    return _send(src, timeout)


def ping(timeout=5.0):
    try:
        body = run_lua('return "pong -- pawn=" .. tostring(pawn() ~= nil)', timeout=timeout)
        return True, body.strip()
    except BridgeError as e:
        return False, str(e)
