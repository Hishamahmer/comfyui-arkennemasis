"""Private installation settings, shared by the gateway and its local launcher."""

import json
import os
import secrets
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

SERVICE_DIR = Path(__file__).resolve().parent
PACK_DIR = SERVICE_DIR.parent
COMFY_DIR = PACK_DIR.parent.parent
DEFAULT_CONFIG = SERVICE_DIR / ".local" / "config.json"
DEFAULT_SCOPES = ("comfy:read", "comfy:write", "comfy:run", "comfy:media")
SCOPES = (*DEFAULT_SCOPES, "comfy:develop", "comfy:maintain")


def local_url(value):
    parsed = urlsplit(value)
    return (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and not parsed.username and not parsed.password and not parsed.query
            and not parsed.fragment and parsed.path in {"", "/"})


def https_url(value):
    parsed = urlsplit(value)
    return (parsed.scheme == "https" and bool(parsed.hostname)
            and not parsed.username and not parsed.password and not parsed.query
            and not parsed.fragment)


@dataclass
class Settings:
    comfy_url: str = "http://127.0.0.1:8188"
    host: str = "127.0.0.1"
    port: int = 8190
    auth_mode: str = "local_token"
    local_token: str = field(default="", repr=False)
    bridge_token: str = field(default="", repr=False)
    connection_token: str = field(default="", repr=False)
    public_url: str = ""
    issuer_url: str = ""
    jwks_url: str = ""
    audience: str = ""
    scope_claim: str = "scope"
    allowed_algorithms: list[str] = field(default_factory=lambda: ["RS256"])
    allowed_subjects: list[str] = field(default_factory=list)
    workflow_root: str = str(COMFY_DIR / "user" / "default" / "workflows")
    state_dir: str = str(SERVICE_DIR / ".local")
    max_media_bytes: int = 20 * 1024 * 1024
    enabled_scopes: list[str] = field(default_factory=lambda: list(DEFAULT_SCOPES))
    allowed_node_packs: list[str] = field(default_factory=list)
    comfy_root: str = str(COMFY_DIR)
    comfy_python: str = ""
    model_roots: dict[str, str] = field(default_factory=dict)
    download_hosts: list[str] = field(default_factory=lambda: ["huggingface.co", "hf.co", "civitai.com"])

    def validate(self, transport="http"):
        if not isinstance(self.enabled_scopes, list) or any(not isinstance(s, str) or s not in SCOPES for s in self.enabled_scopes):
            raise ValueError("enabled_scopes contains an unsupported capability.")
        if not isinstance(self.allowed_node_packs, list) or any(
                not isinstance(pack, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", pack)
                or pack.endswith((".", " ")) for pack in self.allowed_node_packs):
            raise ValueError("allowed_node_packs must contain explicit custom-node folder names.")
        if not Path(self.comfy_root).is_absolute():
            raise ValueError("comfy_root must be an absolute installation path.")
        if self.comfy_python and not Path(self.comfy_python).is_absolute():
            raise ValueError("comfy_python must be an absolute executable path.")
        if not isinstance(self.model_roots, dict) or any(
                not isinstance(key, str) or not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key) or not Path(value).is_absolute()
                for key, value in self.model_roots.items()):
            raise ValueError("model_roots maps model categories to absolute local directories.")
        if not isinstance(self.download_hosts, list) or any(
                not isinstance(host, str) or not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", host)
                for host in self.download_hosts):
            raise ValueError("download_hosts must contain HTTPS provider hostnames without paths.")
        if not local_url(self.comfy_url):
            raise ValueError("comfy_url must point to a loopback HTTP ComfyUI server.")
        if self.host not in {"127.0.0.1", "::1"}:
            raise ValueError("The gateway binds to loopback only. Forward it through a tunnel for web access.")
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise ValueError("port must be between 1024 and 65535.")
        if self.auth_mode not in {"local_token", "oauth", "connection_link"}:
            raise ValueError("auth_mode must be local_token, connection_link or oauth.")
        if len(self.bridge_token) < 32:
            raise ValueError("Missing bridge_token. Run the init command first.")
        if not 1024 <= self.max_media_bytes <= 100 * 1024 * 1024:
            raise ValueError("max_media_bytes must be between 1 KB and 100 MB.")
        if transport != "stdio":
            if self.auth_mode == "local_token":
                if len(self.local_token) < 32:
                    raise ValueError("Missing local_token. Run the init command first.")
                if self.public_url:
                    raise ValueError("A public URL requires auth_mode=oauth; local tokens are for local clients.")
            elif self.auth_mode == "connection_link":
                if not re.fullmatch(r"[A-Za-z0-9_-]{48,128}", self.connection_token):
                    raise ValueError("Create a private connection URL with the web command first.")
                if len(self.local_token) < 32:
                    raise ValueError("Missing local_token. Run the init command first.")
                if self.public_url and (not https_url(self.public_url) or urlsplit(self.public_url).path not in {"", "/"}):
                    raise ValueError("public_url must be an HTTPS origin.")
            else:
                for name in ("public_url", "issuer_url", "jwks_url", "audience"):
                    if not https_url(getattr(self, name)):
                        raise ValueError(f"OAuth {name} must be an explicit HTTPS URL.")
                if urlsplit(self.public_url).path not in {"", "/"}:
                    raise ValueError("public_url must be an HTTPS origin; the MCP path is /mcp.")
                if self.audience != self.endpoint:
                    raise ValueError("audience must equal the public MCP URL, including /mcp.")
                if not self.allowed_subjects or any(not isinstance(s, str) or not s for s in self.allowed_subjects):
                    raise ValueError("Set allowed_subjects to your OAuth user ID before enabling web access.")
        return self

    @property
    def endpoint(self):
        host = f"[{self.host}]" if ":" in self.host else self.host
        path = f"/connect/{self.connection_token}/mcp" if self.auth_mode == "connection_link" else "/mcp"
        return (self.public_url or f"http://{host}:{self.port}").rstrip("/") + path

    @property
    def display_endpoint(self):
        return self.endpoint.replace(self.connection_token, "[private]") if self.connection_token else self.endpoint

    @property
    def backup_root(self):
        return Path(self.state_dir) / "backups"


def load_settings(path=DEFAULT_CONFIG, transport="http"):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Configuration is missing. Run init first: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a JSON object.")
    settings = Settings(**data)
    settings.bridge_token = os.environ.get("ARK_MCP_BRIDGE_TOKEN", settings.bridge_token)
    settings.local_token = os.environ.get("ARK_MCP_LOCAL_TOKEN", settings.local_token)
    settings.connection_token = os.environ.get("ARK_MCP_CONNECTION_TOKEN", settings.connection_token)
    return settings.validate(transport)


def initialize(path=DEFAULT_CONFIG):
    """Create a private configuration once; never replace existing credentials."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = Settings(local_token=secrets.token_urlsafe(36), bridge_token=secrets.token_urlsafe(36),
                        state_dir=str(path.parent))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(settings.__dict__, handle, indent=2)
        handle.write("\n")
    return settings


def enable_connection_link(path=DEFAULT_CONFIG, *, public_url=""):
    """Enable a capability URL without replacing local/bridge credentials."""
    path = Path(path)
    settings = load_settings(path, transport="stdio")
    settings.auth_mode = "connection_link"
    settings.public_url = public_url.rstrip("/")
    settings.connection_token = settings.connection_token or secrets.token_urlsafe(48)
    settings.validate()
    save_settings(settings, path)
    return settings


def save_settings(settings, path=DEFAULT_CONFIG):
    """Save owner configuration atomically without changing any credentials."""
    settings.validate()
    path = Path(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(settings.__dict__, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return settings
