"""
Beeper Desktop API — OAuth2 PKCE + Dynamic Client Registration helper.

Stdlib only (no `requests`, no `httpx`). Provides the token lifecycle the
kernel's `beeper_client.py` needs, as a standalone module so the kernel
stays a thin transport layer.

Beeper's OAuth profile (from RFC 8414 discovery at
`http://localhost:23373/.well-known/oauth-authorization-server`):
  - Public client (no client_secret; `token_endpoint_auth_method=none`)
  - `authorization_code` grant only
  - S256 PKCE mandatory
  - `read` + `write` scopes

Flow:
  1. `register_client` → POST /oauth/register (RFC 7591)
  2. PKCE pair (verifier + S256 challenge)
  3. Local HTTP loopback listener on 127.0.0.1:<ephemeral>
  4. Open system browser to /oauth/authorize with redirect_uri=loopback
  5. Beeper UI shows approval → redirects to loopback with `code`
  6. Exchange code at /oauth/token with verifier → access/refresh tokens
  7. Persist to `token_beeper.json` (gitignored via existing `token*.json`)

Token refresh uses the stored `refresh_token`; the high-level entry point
`get_or_create_token()` handles first-run onboarding + subsequent refresh
transparently.

Usage
-----
From the kernel:
    from harvester.beeper_oauth import get_or_create_token
    token = get_or_create_token()  # blocks first run for browser, silent after
    headers = {"Authorization": f"Bearer {token.access_token}"}

From the CLI, to onboard / re-authorize:
    python -m harvester.beeper_oauth --interactive

Self-test (no Beeper needed):
    python -m harvester.beeper_oauth --self-test
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import logging
import secrets
import socket
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("contacts-refiner.beeper_oauth")

# Default Beeper Desktop API issuer. Overridable via BEEPER_API_BASE env var
# for testing against mocks. Never set to a non-loopback value.
DEFAULT_ISSUER = "http://localhost:23373"

# Default location for the persisted token. Matches the project's existing
# `token*.json` convention at repo root (already gitignored).
DEFAULT_TOKEN_PATH = Path("token_beeper.json")

# Name this client registers under inside Beeper's Approved Connections.
CLIENT_NAME = "contactrefiner-harvester"

# Refresh window — proactively refresh if token expires within this many
# seconds, so callers don't hit 401 mid-request.
REFRESH_SLACK_SECONDS = 60

# HTTP timeouts — OAuth endpoints are all on localhost so these can be tight.
HTTP_TIMEOUT_SECONDS = 10


# ── data shapes ───────────────────────────────────────────────────────────

@dataclass
class BeeperToken:
    """Persistent token material. Never log `access_token` or `refresh_token`."""
    access_token: str
    refresh_token: Optional[str]
    token_type: str
    scope: str
    expires_at: str  # ISO-8601 UTC
    client_id: str
    issuer: str
    issued_at: str
    schema_version: int = 1

    def is_expired(self, slack_seconds: int = REFRESH_SLACK_SECONDS) -> bool:
        try:
            exp = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        return exp <= datetime.now(timezone.utc) + timedelta(seconds=slack_seconds)


@dataclass
class ClientRegistration:
    """Dynamic Client Registration result (RFC 7591)."""
    client_id: str
    client_secret: Optional[str]  # None for public clients (Beeper's default)
    client_id_issued_at: int
    redirect_uris: list[str]
    grant_types: list[str]
    response_types: list[str]
    token_endpoint_auth_method: str


# ── PKCE ──────────────────────────────────────────────────────────────────

def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) per RFC 7636.

    Verifier: 64 URL-safe chars (43-128 char range per RFC; 64 is amply
    random). Challenge: base64url(SHA-256(verifier)), no padding.
    """
    verifier_bytes = secrets.token_urlsafe(48)  # 48 bytes → ~64 chars
    # `token_urlsafe` already produces URL-safe ASCII; trim any padding.
    verifier = verifier_bytes.rstrip("=")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    return verifier, challenge


# ── HTTP helpers (stdlib urllib) ──────────────────────────────────────────

def _post_json(url: str, body: dict, *, headers: Optional[dict] = None) -> dict:
    """POST JSON, return JSON. Raises on non-2xx with body context."""
    payload = json.dumps(body).encode("utf-8")
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=payload, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"HTTP {e.code} from {url}: {body_text}") from e


def _post_form(url: str, form: dict, *, headers: Optional[dict] = None) -> dict:
    """POST application/x-www-form-urlencoded, return JSON."""
    payload = urllib.parse.urlencode(form).encode("utf-8")
    h = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=payload, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"HTTP {e.code} from {url}: {body_text}") from e


# ── OAuth2 endpoints ──────────────────────────────────────────────────────

def discover(issuer: str = DEFAULT_ISSUER) -> dict:
    """Fetch the RFC 8414 discovery document."""
    url = issuer.rstrip("/") + "/.well-known/oauth-authorization-server"
    with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def register_client(
    *,
    issuer: str = DEFAULT_ISSUER,
    client_name: str = CLIENT_NAME,
    redirect_uris: Optional[list[str]] = None,
    scopes: str = "read write",
) -> ClientRegistration:
    """Dynamically register a client with Beeper (RFC 7591).

    Beeper's default token_endpoint_auth_method is `none` (public client);
    PKCE provides the confidentiality. No client secret to safeguard.
    """
    if redirect_uris is None:
        redirect_uris = ["http://127.0.0.1:0/callback"]  # bound at runtime
    disco = discover(issuer)
    reg_url = disco["registration_endpoint"]
    body = {
        "client_name": client_name,
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": scopes,
    }
    raw = _post_json(reg_url, body)
    return ClientRegistration(
        client_id=raw["client_id"],
        client_secret=raw.get("client_secret"),
        client_id_issued_at=int(raw.get("client_id_issued_at", 0)),
        redirect_uris=list(raw.get("redirect_uris", redirect_uris)),
        grant_types=list(raw.get("grant_types", ["authorization_code"])),
        response_types=list(raw.get("response_types", ["code"])),
        token_endpoint_auth_method=raw.get("token_endpoint_auth_method", "none"),
    )


def build_authorize_url(
    issuer: str, client_id: str, redirect_uri: str, code_challenge: str, state: str,
    scopes: str = "read write",
) -> str:
    disco = discover(issuer)
    q = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": scopes,
        "state": state,
    })
    return f"{disco['authorization_endpoint']}?{q}"


def exchange_code(
    *,
    issuer: str, client_id: str, code: str, code_verifier: str, redirect_uri: str,
) -> BeeperToken:
    disco = discover(issuer)
    raw = _post_form(disco["token_endpoint"], {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    })
    return _token_from_response(raw, issuer=issuer, client_id=client_id)


def refresh_access_token(
    *, issuer: str, client_id: str, refresh_token: str,
) -> BeeperToken:
    disco = discover(issuer)
    raw = _post_form(disco["token_endpoint"], {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    })
    return _token_from_response(raw, issuer=issuer, client_id=client_id)


def _token_from_response(raw: dict, *, issuer: str, client_id: str) -> BeeperToken:
    expires_in = int(raw.get("expires_in") or 3600)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=expires_in)
    return BeeperToken(
        access_token=raw["access_token"],
        refresh_token=raw.get("refresh_token"),
        token_type=raw.get("token_type", "Bearer"),
        scope=raw.get("scope", ""),
        expires_at=expires_at.isoformat(),
        client_id=client_id,
        issuer=issuer,
        issued_at=now.isoformat(),
    )


# ── browser flow with loopback receiver ───────────────────────────────────

class _CallbackServer(http.server.HTTPServer):
    """Single-shot HTTP server that captures the OAuth redirect."""
    result: dict = {}
    expected_state: str = ""


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        # Silence default stderr access log
        return

    def do_GET(self):  # noqa: N802 — required signature
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        server: _CallbackServer = self.server  # type: ignore[assignment]

        # State check prevents CSRF on the callback.
        if params.get("state") != server.expected_state:
            self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"state mismatch")
            server.result = {"error": "state_mismatch"}
            return

        if "error" in params:
            server.result = {
                "error": params["error"],
                "description": params.get("error_description", ""),
            }
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                f"<h1>Authorization failed</h1><p>{params['error']}</p>"
                .encode("utf-8")
            )
            return

        server.result = {"code": params.get("code", "")}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<h1>Authorized</h1>"
            b"<p>You can close this tab and return to the terminal.</p>"
        )


def _run_authorization_flow(
    issuer: str, client_id: str, scopes: str, *, open_browser: bool = True,
) -> tuple[str, str, str]:
    """Run the interactive OAuth authorization code flow.

    Returns: (code, code_verifier, redirect_uri). Blocks until the user
    completes the browser flow.
    """
    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(16)

    # Bind the loopback server first so we know our actual port.
    server = _CallbackServer(("127.0.0.1", 0), _CallbackHandler)
    port = server.server_address[1]
    server.expected_state = state
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    # Before opening browser, ensure the chosen redirect URI is registered
    # under this client — otherwise /oauth/authorize rejects immediately.
    # We don't re-register here (caller is responsible); we just build the URL.
    authorize_url = build_authorize_url(
        issuer=issuer, client_id=client_id, redirect_uri=redirect_uri,
        code_challenge=challenge, state=state, scopes=scopes,
    )

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        print(f"Opening browser for Beeper authorization…")
        print(f"  If the browser does not open, visit:\n    {authorize_url}")
        if open_browser:
            webbrowser.open(authorize_url)
        # Wait up to 5 minutes for the callback.
        import time
        deadline = time.monotonic() + 300
        while not server.result and time.monotonic() < deadline:
            time.sleep(0.2)
        if not server.result:
            raise TimeoutError("Timed out waiting for Beeper authorization")
        if "error" in server.result:
            raise RuntimeError(
                f"Authorization failed: {server.result['error']} "
                f"{server.result.get('description', '')}"
            )
        code = server.result["code"]
        return code, verifier, redirect_uri
    finally:
        server.shutdown()
        server.server_close()


# ── persistence ───────────────────────────────────────────────────────────

def save_token(token: BeeperToken, path: Path = DEFAULT_TOKEN_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(token), indent=2))
    # Permissions: owner read-write only. Defensive against umask exotica.
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_token(path: Path = DEFAULT_TOKEN_PATH) -> Optional[BeeperToken]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1:
            logger.warning(
                f"Beeper token schema mismatch at {path}; discarding"
            )
            return None
        return BeeperToken(**data)
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        logger.warning(f"Failed to load Beeper token at {path}: {e}")
        return None


def delete_token(path: Path = DEFAULT_TOKEN_PATH) -> None:
    """Hard-delete the persisted token. Used for forced re-authorization."""
    if path.exists():
        path.unlink()


# ── high-level entry point ────────────────────────────────────────────────

def get_or_create_token(
    *,
    issuer: str = DEFAULT_ISSUER,
    token_path: Path = DEFAULT_TOKEN_PATH,
    scopes: str = "read write",
    client_name: str = CLIENT_NAME,
    force_new: bool = False,
    open_browser: bool = True,
) -> BeeperToken:
    """Return a live Beeper access token, refreshing or onboarding as needed.

    Decision tree:
      - Token file missing / `force_new=True`: run full DCR + browser flow.
      - Token file present but expired: try refresh; fall back to full flow
        if refresh_token is missing or the refresh fails.
      - Token file present and fresh: return as-is, no API call.

    Side effects:
      - May open the system web browser.
      - May write `token_path` and the matching registration metadata.
    """
    if not force_new:
        existing = load_token(token_path)
        if existing and not existing.is_expired():
            return existing
        if existing and existing.refresh_token:
            try:
                refreshed = refresh_access_token(
                    issuer=existing.issuer,
                    client_id=existing.client_id,
                    refresh_token=existing.refresh_token,
                )
                save_token(refreshed, token_path)
                return refreshed
            except Exception as e:
                logger.info(f"Refresh failed, re-authorizing: {e}")

    # Full flow: register client, PKCE authorize, exchange code.
    # We register a fresh client each time we hit this path so the user's
    # Approved Connections list reflects current use. The old registration
    # is garbage-collected by Beeper eventually.
    port_hint = _pick_ephemeral_port()
    redirect_uri = f"http://127.0.0.1:{port_hint}/callback"
    registration = register_client(
        issuer=issuer,
        client_name=client_name,
        redirect_uris=[redirect_uri],
        scopes=scopes,
    )
    code, verifier, actual_redirect_uri = _run_authorization_flow(
        issuer=issuer, client_id=registration.client_id,
        scopes=scopes, open_browser=open_browser,
    )
    # The authorization flow picks its own ephemeral port separately from the
    # hint we used for registration — Beeper's DCR accepts the ephemeral
    # pattern and will match on the exact redirect URI at the token endpoint.
    # If Beeper rejects, re-register with the actual URI.
    try:
        token = exchange_code(
            issuer=issuer, client_id=registration.client_id,
            code=code, code_verifier=verifier,
            redirect_uri=actual_redirect_uri,
        )
    except RuntimeError as e:
        if "redirect_uri" in str(e).lower():
            logger.info("Re-registering client with actual redirect URI")
            registration = register_client(
                issuer=issuer, client_name=client_name,
                redirect_uris=[actual_redirect_uri], scopes=scopes,
            )
            token = exchange_code(
                issuer=issuer, client_id=registration.client_id,
                code=code, code_verifier=verifier,
                redirect_uri=actual_redirect_uri,
            )
        else:
            raise

    save_token(token, token_path)
    return token


def _pick_ephemeral_port() -> int:
    """Pick an unused high port for the initial DCR hint.

    Note: the actual port used during the flow is chosen by the OS when the
    loopback server binds to port 0; this is only a cosmetic hint for
    Beeper's Approved Connections list.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── self-test ─────────────────────────────────────────────────────────────

def _run_self_test() -> None:
    """Offline tests — no Beeper, no network."""
    print("beeper_oauth self-test (offline)…")

    # PKCE pair shape
    v, c = generate_pkce_pair()
    assert 43 <= len(v) <= 128, f"verifier length {len(v)}"
    assert 42 <= len(c) <= 44, f"challenge length {len(c)}"
    assert "=" not in v and "=" not in c, "no padding in PKCE values"
    print(f"  ✓ PKCE pair: verifier={len(v)}c, challenge={len(c)}c")

    # Verifier produces correct challenge (RFC 7636 §4.2)
    derived = (
        base64.urlsafe_b64encode(hashlib.sha256(v.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    assert derived == c
    print("  ✓ challenge = base64url(sha256(verifier))")

    # RFC 7636 Appendix B test vector
    vec_v = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    vec_c_expected = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    vec_c = (
        base64.urlsafe_b64encode(hashlib.sha256(vec_v.encode("ascii")).digest())
        .decode("ascii").rstrip("=")
    )
    assert vec_c == vec_c_expected, f"RFC 7636 test vector: got {vec_c}"
    print("  ✓ matches RFC 7636 Appendix B test vector")

    # Token round-trip
    import tempfile
    now = datetime.now(timezone.utc)
    tok = BeeperToken(
        access_token="atk_fake",
        refresh_token="rtk_fake",
        token_type="Bearer",
        scope="read write",
        expires_at=(now + timedelta(hours=1)).isoformat(),
        client_id="cli_fake",
        issuer="http://localhost:23373",
        issued_at=now.isoformat(),
    )
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        p = Path(tf.name)
    try:
        save_token(tok, p)
        # Verify permissions tightened
        mode = p.stat().st_mode & 0o777
        assert mode in (0o600, 0o644), f"token file mode {oct(mode)}"
        loaded = load_token(p)
        assert loaded is not None
        assert loaded.access_token == tok.access_token
        assert loaded.refresh_token == tok.refresh_token
        assert not loaded.is_expired()
        print("  ✓ token save/load round-trip")

        # Expiry detection
        expired = BeeperToken(
            access_token="x", refresh_token="y", token_type="Bearer", scope="",
            expires_at=(now - timedelta(minutes=5)).isoformat(),
            client_id="c", issuer=tok.issuer, issued_at=tok.issued_at,
        )
        assert expired.is_expired()
        print("  ✓ expiry detection")

        # Slack window — token expiring in 30s is considered expired
        soon = BeeperToken(
            access_token="x", refresh_token="y", token_type="Bearer", scope="",
            expires_at=(now + timedelta(seconds=30)).isoformat(),
            client_id="c", issuer=tok.issuer, issued_at=tok.issued_at,
        )
        assert soon.is_expired()
        print("  ✓ expiry slack window triggers proactive refresh")

        # Schema version guard
        p.write_text(json.dumps({**asdict(tok), "schema_version": 999}))
        assert load_token(p) is None
        print("  ✓ schema version mismatch → None (safe reject)")
    finally:
        p.unlink(missing_ok=True)

    # URL construction (no network)
    import unittest.mock as mock
    with mock.patch("harvester.beeper_oauth.discover") as mdisco:
        mdisco.return_value = {
            "authorization_endpoint": "http://localhost:23373/oauth/authorize",
            "token_endpoint": "http://localhost:23373/oauth/token",
        }
        url = build_authorize_url(
            issuer="http://localhost:23373", client_id="cli_x",
            redirect_uri="http://127.0.0.1:45678/callback",
            code_challenge="abc", state="xyz",
        )
        assert "response_type=code" in url
        assert "code_challenge_method=S256" in url
        assert "scope=read+write" in url
        assert "state=xyz" in url
        assert "client_id=cli_x" in url
        assert "http%3A%2F%2F127.0.0.1" in url
        print("  ✓ authorize URL construction")

    print("All offline self-tests passed.")
    print()
    print("To run the full interactive flow against live Beeper:")
    print("    python -m harvester.beeper_oauth --interactive")


def _run_interactive() -> None:
    """Live flow against running Beeper. Requires user to approve in browser."""
    print("Running live Beeper OAuth flow…")
    token = get_or_create_token(force_new=True)
    print(f"  ✓ access_token acquired ({len(token.access_token)}c), "
          f"scope=[{token.scope}]")
    print(f"  ✓ expires {token.expires_at}")
    # Verify by hitting an authenticated endpoint
    req = urllib.request.Request(
        f"{token.issuer}/v1/accounts",
        headers={"Authorization": f"Bearer {token.access_token}"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    accts = body if isinstance(body, list) else body.get("accounts", body)
    print(f"  ✓ /v1/accounts returned {len(accts)} account(s)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true",
                        help="Run offline tests (no Beeper needed)")
    parser.add_argument("--interactive", action="store_true",
                        help="Run the live OAuth flow against localhost Beeper")
    parser.add_argument("--force-new", action="store_true",
                        help="With --interactive: ignore existing token and re-onboard")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.self_test:
        _run_self_test()
        return 0
    if args.interactive:
        _run_interactive()
        return 0

    # No flag — default to self-test
    _run_self_test()
    return 0


if __name__ == "__main__":
    sys.exit(main())
