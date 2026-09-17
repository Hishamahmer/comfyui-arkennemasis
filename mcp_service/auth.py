"""Bearer authentication for the optional MCP gateway, separate from Codex login."""

import asyncio
import hmac
import json
import math
import re
import time
from collections.abc import Sequence
from urllib.parse import urlsplit

import httpx
import jwt
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken


_SCOPE = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]+\Z")
_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"})


def _https_url(value: str, name: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.fragment or any(c.isspace() for c in value)):
        raise ValueError(f"{name} must be an HTTPS URL without credentials or a fragment.")
    # Parsing the port also rejects malformed/out-of-range values before startup.
    parsed.port
    return value


def _scopes(value: str | Sequence[str]) -> list[str]:
    values = value.split() if isinstance(value, str) else value
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("At least one OAuth scope is required.")
    if any(not isinstance(scope, str) or not _SCOPE.fullmatch(scope) for scope in values):
        raise ValueError("OAuth scopes must be nonempty scope tokens.")
    return list(dict.fromkeys(values))


class LocalTokenVerifier:
    """A generated bearer secret for loopback-only development connections."""

    def __init__(self, token: str, scopes: Sequence[str]):
        if not isinstance(token, str) or len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
            raise ValueError("The local bearer token must be a generated secret of at least 32 ASCII characters.")
        self._token = token
        self._scopes = _scopes(scopes)

    async def verify_token(self, token: str) -> AccessToken | None:
        if not isinstance(token, str) or not token.isascii() or not hmac.compare_digest(self._token, token):
            return None
        return AccessToken(token=token, client_id="local", subject="local-owner", scopes=list(self._scopes))


class OAuthTokenVerifier:
    """Verify issuer-signed JWT access tokens against one configured JWKS endpoint.

    Login, user consent, token issuance and revocation remain with the external
    authorization server. Never fetch a URL supplied by an access token.
    """

    def __init__(
        self,
        issuer_url: str,
        audience: str,
        jwks_url: str,
        allowed_algorithms: Sequence[str] = ("RS256",),
        required_scopes: Sequence[str] = (),
        allowed_subjects: Sequence[str] = (),
        scope_claim: str = "scope",
        jwks_ttl_seconds: int = 300,
        client: httpx.AsyncClient | None = None,
    ):
        self.issuer_url = _https_url(issuer_url, "OAuth issuer")
        self.jwks_url = _https_url(jwks_url, "JWKS endpoint")
        if not audience or any(c.isspace() for c in audience):
            raise ValueError("An explicit OAuth audience is required.")
        if not allowed_algorithms or not set(allowed_algorithms) <= _ALGORITHMS:
            raise ValueError("OAuth algorithms must be explicitly allowed asymmetric signing algorithms.")
        if not scope_claim or any(c.isspace() for c in scope_claim):
            raise ValueError("An OAuth scope claim name is required.")
        if not 30 <= jwks_ttl_seconds <= 3600:
            raise ValueError("JWKS cache duration must be between 30 and 3600 seconds.")
        if isinstance(allowed_subjects, str) or any(not isinstance(subject, str) or not subject.strip() for subject in allowed_subjects):
            raise ValueError("Allowed OAuth subjects must be a list of nonempty subject identifiers.")
        self.audience = audience
        self.allowed_algorithms = tuple(allowed_algorithms)
        self.required_scopes = set(_scopes(required_scopes)) if required_scopes else set()
        self.allowed_subjects = frozenset(allowed_subjects)
        self.scope_claim = scope_claim
        self._ttl = jwks_ttl_seconds
        self._owns_client = client is None
        self._client = client if client is not None else httpx.AsyncClient(timeout=10.0, follow_redirects=False, trust_env=False)
        self._keys: list[dict] = []
        self._expires = 0.0
        self._last_attempt = float("-inf")
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _refresh_keys(self) -> None:
        body = bytearray()
        async with self._client.stream("GET", self.jwks_url, timeout=10.0, follow_redirects=False) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 1_048_576:
                    raise ValueError("JWKS response is too large.")
        document = json.loads(body)
        keys = document.get("keys") if isinstance(document, dict) else None
        if not isinstance(keys, list) or not 1 <= len(keys) <= 100 or any(not isinstance(key, dict) for key in keys):
            raise ValueError("JWKS response must contain a signing key list.")
        self._keys = keys
        self._expires = time.monotonic() + self._ttl

    async def _signing_key(self, kid: str, algorithm: str):
        async with self._lock:
            now = time.monotonic()
            known = any(key.get("kid") == kid for key in self._keys)
            # Unknown key ids trigger a bounded refresh for issuer key rotation.
            if (now >= self._expires or not known) and now - self._last_attempt >= 30:
                self._last_attempt = now
                await self._refresh_keys()
            if now >= self._expires:
                return None
            matching = [key for key in self._keys if key.get("kid") == kid]
            if len(matching) != 1:
                return None
            key = matching[0]
            if key.get("use", "sig") != "sig" or key.get("alg", algorithm) != algorithm:
                return None
            if "key_ops" in key and (not isinstance(key["key_ops"], list) or "verify" not in key["key_ops"]):
                return None
            return jwt.PyJWK.from_dict(key, algorithm=algorithm).key

    async def verify_token(self, token: str) -> AccessToken | None:
        if not isinstance(token, str) or not token or len(token) > 32768:
            return None
        try:
            header = jwt.get_unverified_header(token)
            algorithm, kid = header.get("alg"), header.get("kid")
            if (algorithm not in self.allowed_algorithms or not isinstance(kid, str)
                    or not kid or len(kid) > 256 or header.get("crit")):
                return None
            key = await self._signing_key(kid, algorithm)
            if key is None:
                return None
            claims = jwt.decode(
                token,
                key,
                algorithms=self.allowed_algorithms,
                audience=self.audience,
                issuer=self.issuer_url,
                options={"require": ["iss", "aud", "exp", "sub"], "enforce_minimum_key_length": True},
            )
            expiry, subject = claims["exp"], claims["sub"]
            if (isinstance(expiry, bool) or not isinstance(expiry, (int, float))
                    or not math.isfinite(expiry) or not isinstance(subject, str) or not subject):
                return None
            if self.allowed_subjects and subject not in self.allowed_subjects:
                return None
            scopes = _scopes(claims.get(self.scope_claim))
            if not self.required_scopes.issubset(scopes):
                return None
            client_id = claims.get("client_id", claims.get("azp", subject))
            if not isinstance(client_id, str) or not client_id:
                return None
            return AccessToken(
                token=token, client_id=client_id, subject=subject, scopes=scopes,
                expires_at=int(expiry), resource=self.audience, claims={"iss": self.issuer_url},
            )
        except (jwt.PyJWTError, httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError):
            return None


def require_scope(scope: str) -> AccessToken:
    """Check the SDK's request-local identity before a tool performs its operation."""
    token = get_access_token()
    if token is None:
        raise PermissionError("Authentication is required.")
    if scope not in token.scopes:
        raise PermissionError(f"This operation requires the {scope} scope.")
    return token
