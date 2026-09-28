"""Owner-staged external OAuth configuration and a local client denylist.

This module deliberately needs only the standard library. It runs in ComfyUI
without installing the optional gateway's JWT or MCP dependencies there.
"""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading

from .settings import DEFAULT_CONFIG, Settings, load_settings, save_settings

FIELDS = ("public_url", "issuer_url", "jwks_url", "audience", "allowed_subjects", "allowed_algorithms", "scope_claim")
MAX_DOCUMENT = 1024 * 1024
_LOCK = threading.RLock()


def client_identifier(value):
    """OAuth client IDs are opaque identifiers; CIMD IDs can be HTTPS URLs."""
    if not isinstance(value, str) or not 1 <= len(value) <= 1024 or any(not 33 <= ord(c) <= 126 for c in value):
        raise ValueError("Enter the exact OAuth client ID, using 1–1024 printable ASCII characters without spaces.")
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate configuration property.")
        result[key] = value
    return result


def _document(path):
    path = Path(path)
    with path.open("rb") as handle:
        raw = handle.read(MAX_DOCUMENT + 1)
    if len(raw) > MAX_DOCUMENT:
        raise ValueError("OAuth configuration is too large.")
    try:
        value = json.loads(raw, object_pairs_hook=_unique)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("OAuth configuration contains invalid JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("OAuth configuration must be an object.")
    return value, hashlib.sha256(raw).hexdigest()


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".oauth-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class OAuthClients:
    """Changes take effect on the gateway's next token verification, without restart.

Missing state means no locally denied clients. Corrupt or unreadable state raises
so the caller can reject authentication instead of silently removing revocations.
"""

    def __init__(self, path):
        self.path = Path(path)

    def denied(self):
        try:
            value, _ = _document(self.path)
        except FileNotFoundError:
            return []
        if set(value) != {"version", "denied_client_ids"} or type(value["version"]) is not int or value["version"] != 1:
            raise ValueError("OAuth client revocations have an unsupported format.")
        identifiers = value["denied_client_ids"]
        if not isinstance(identifiers, list) or len(identifiers) > 512:
            raise ValueError("OAuth client revocations must contain at most 512 client IDs.")
        return sorted({client_identifier(identifier) for identifier in identifiers})

    def set_revoked(self, identifier, revoked):
        identifier = client_identifier(identifier)
        if type(revoked) is not bool:
            raise ValueError("revoked must be true or false.")
        with _LOCK:
            identifiers = set(self.denied())
            if revoked:
                identifiers.add(identifier)
            else:
                identifiers.discard(identifier)
            if len(identifiers) > 512:
                raise ValueError("There are already 512 locally revoked OAuth clients.")
            _atomic_json(self.path, {"version": 1, "denied_client_ids": sorted(identifiers)})
        return {"client_id": identifier, "revoked": revoked, "effective": "next_request",
                "message": "Client access is blocked locally." if revoked else "The local block is removed; provider permissions still apply."}


class OAuthSetup:
    def __init__(self, config_path=DEFAULT_CONFIG):
        self.config_path = Path(config_path)
        self.draft_path = self.config_path.parent / "oauth-setup.json"

    def _current(self):
        _, revision = _document(self.config_path)
        try:
            return load_settings(self.config_path, transport="stdio"), revision
        except (TypeError, ValueError, OSError) as exc:
            raise ValueError("The installation configuration is invalid. Repair it in MCP setup before configuring OAuth.") from exc

    @staticmethod
    def _provider(settings):
        return {field: getattr(settings, field) for field in FIELDS}

    def _validated(self, provider, current):
        if not isinstance(provider, dict) or set(provider) != set(FIELDS):
            raise ValueError("Supply only the seven displayed OAuth provider settings.")
        candidate = Settings(**{**current.__dict__, **provider, "auth_mode": "oauth"})
        candidate.validate()
        return candidate

    def status(self):
        state = {"configured": False, "auth_mode": "unconfigured", "provider": {}, "draft": {},
                 "draft_revision": None, "config_revision": None, "revoked_clients": [],
                 "errors": [], "requires_provider_setup": True}
        if not self.config_path.is_file():
            state["message"] = "Save the installation in MCP setup first. OAuth additionally needs an external identity provider."
            return state
        try:
            current, revision = self._current()
        except (ValueError, OSError) as exc:
            state["errors"].append(str(exc) if isinstance(exc, ValueError) else "Cannot read installation configuration.")
            state["message"] = "Repair the local installation configuration before setting up OAuth."
            return state
        state.update(configured=True, auth_mode=current.auth_mode, provider=self._provider(current),
                     config_revision=revision, enabled_scopes=current.enabled_scopes,
                     fallback_available=bool(current.connection_token))
        if current.auth_mode == "oauth":
            try:
                current.validate()
                state["requires_provider_setup"] = False
            except ValueError as exc:
                state["errors"].append(str(exc))
        try:
            draft, draft_revision = _document(self.draft_path)
            candidate = self._validated(draft, current)
            state.update(draft=self._provider(candidate), draft_revision=draft_revision)
        except FileNotFoundError:
            pass
        except (ValueError, OSError, TypeError):
            state["errors"].append("The saved OAuth draft is invalid. Save corrected provider settings to replace it.")
        try:
            state["revoked_clients"] = OAuthClients(Path(current.state_dir) / "oauth-clients.json").denied()
        except (ValueError, OSError):
            state["errors"].append("OAuth client revocations cannot be read. OAuth requests will be rejected until this file is repaired locally.")
        state["message"] = ("OAuth is saved as the connection mode. A running gateway keeps its current configuration until restarted."
                            if current.auth_mode == "oauth" else "OAuth is optional and is not enabled. Saving provider settings only prepares a draft.")
        return state

    def save(self, provider, config_revision):
        with _LOCK:
            current, revision = self._current()
            if config_revision != revision:
                raise ValueError("Installation settings changed. Refresh OAuth setup before saving.")
            candidate = self._validated(provider, current)
            _atomic_json(self.draft_path, self._provider(candidate))
        return {**self.status(), "message": "Provider settings saved as a draft. Test and configure your provider, then choose Enable OAuth."}

    def enable(self, config_revision, draft_revision):
        with _LOCK:
            current, revision = self._current()
            if config_revision != revision:
                raise ValueError("Installation settings changed. Refresh OAuth setup before enabling.")
            try:
                draft, actual_draft = _document(self.draft_path)
            except FileNotFoundError as exc:
                raise ValueError("Save valid OAuth provider settings before enabling OAuth.") from exc
            if draft_revision != actual_draft:
                raise ValueError("OAuth provider settings changed. Refresh and review the latest draft.")
            candidate = self._validated(draft, current)
            # A broken denylist must never be bypassed by enabling a new provider.
            OAuthClients(Path(candidate.state_dir) / "oauth-clients.json").denied()
            save_settings(candidate, self.config_path)
        return {**self.status(), "restart_required": True,
                "message": "OAuth is saved. Restart the AI connection, then use the public /mcp URL with Authentication: OAuth. Existing private-URL clients stop working after restart."}

    def disable(self, config_revision):
        with _LOCK:
            current, revision = self._current()
            if config_revision != revision:
                raise ValueError("Installation settings changed. Refresh OAuth setup before switching modes.")
            if not current.connection_token:
                raise ValueError("There is no saved private connection URL. Configure local access through MCP setup first.")
            current.auth_mode = "connection_link"
            current.validate()
            save_settings(current, self.config_path)
        return {**self.status(), "restart_required": True,
                "message": "Private-URL mode is saved. Restart the AI connection and use its private connection URL with No Auth."}

    def revoke(self, identifier, revoked=True):
        current, _ = self._current()
        return OAuthClients(Path(current.state_dir) / "oauth-clients.json").set_revoked(identifier, revoked)
