"""Explicit owner-only OAuth setup, with no public or tool-accessible mutations."""

from aiohttp import web

from .oauth_setup import OAuthSetup
from .settings import DEFAULT_CONFIG
from .setup_routes import SetupRoutes

PREFIX = "/arkennemasis/mcp/oauth"


def _fields(body, expected):
    if set(body) != set(expected):
        raise ValueError("Unexpected OAuth setup parameters. Refresh the local setup page.")


class OAuthSetupRoutes(SetupRoutes):
    def __init__(self, oauth):
        super().__init__(oauth)
        self.oauth = oauth

    async def status(self, request):
        return await self.response(request, lambda _: self.oauth.status())

    async def save(self, request):
        def action(body):
            _fields(body, {"provider", "config_revision"})
            return self.oauth.save(body["provider"], body["config_revision"])
        return await self.response(request, action, empty=False, mutation=True)

    async def enable(self, request):
        def action(body):
            _fields(body, {"config_revision", "draft_revision"})
            return self.oauth.enable(body["config_revision"], body["draft_revision"])
        return await self.response(request, action, empty=False, mutation=True)

    async def disable(self, request):
        def action(body):
            _fields(body, {"config_revision"})
            return self.oauth.disable(body["config_revision"])
        return await self.response(request, action, empty=False, mutation=True)

    async def revoke(self, request):
        def action(body):
            _fields(body, {"client_id"})
            return self.oauth.revoke(body["client_id"], True)
        return await self.response(request, action, empty=False, mutation=True)

    async def unrevoke(self, request):
        def action(body):
            _fields(body, {"client_id"})
            return self.oauth.revoke(body["client_id"], False)
        return await self.response(request, action, empty=False, mutation=True)


def register_oauth_setup_routes(routes, *, config_path=DEFAULT_CONFIG):
    setup = OAuthSetupRoutes(OAuthSetup(config_path))
    for name in ("status", "save", "enable", "disable", "revoke", "unrevoke"):
        routes.post(f"{PREFIX}/{name}")(getattr(setup, name))
    return setup
