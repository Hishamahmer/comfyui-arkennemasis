import json
import secrets
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from mcp_service.auth import LocalTokenVerifier, OAuthTokenVerifier, require_scope


ISSUER = "https://issuer.example.test/"
JWKS = "https://issuer.example.test/.well-known/jwks.json"
AUDIENCE = "https://arkennemasis.example.test/mcp"


class LocalAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_secret_is_exact_and_has_only_configured_scopes(self):
        secret = secrets.token_urlsafe(32)
        verifier = LocalTokenVerifier(secret, ["read", "run"])
        access = await verifier.verify_token(secret)
        self.assertEqual(access.scopes, ["read", "run"])
        self.assertEqual(access.subject, "local-owner")
        self.assertIsNone(await verifier.verify_token(secrets.token_urlsafe(32)))
        self.assertIsNone(await verifier.verify_token(secret + "\n"))
        self.assertIsNone(await verifier.verify_token("\u2603"))

    async def test_local_configuration_rejects_empty_scopes_and_short_secrets(self):
        with self.assertRaises(ValueError):
            LocalTokenVerifier(secrets.token_urlsafe(8), ["read"])
        with self.assertRaises(ValueError):
            LocalTokenVerifier(secrets.token_urlsafe(32), [])


class OAuthAuthTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    async def asyncSetUp(self):
        self.requests = []
        self.document = {"keys": [self.jwk(self.private_key, "current")]}
        self.status_code = 200
        self.headers = {}
        self.clock = 1000.0
        clock_patch = patch("mcp_service.auth.time", SimpleNamespace(monotonic=lambda: self.clock))
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.respond))
        self.addAsyncCleanup(self.client.aclose)
        self.verifier = OAuthTokenVerifier(ISSUER, AUDIENCE, JWKS, required_scopes=["read"], client=self.client)

    @staticmethod
    def jwk(key, kid):
        return dict(json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())), kid=kid, use="sig", alg="RS256")

    def respond(self, request):
        self.requests.append(request)
        return httpx.Response(self.status_code, json=self.document, headers=self.headers)

    def signed(self, changes=None, missing=(), key=None, kid="current", extra_headers=None):
        claims = {"iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 120,
                  "sub": "test-owner", "client_id": "web-client", "scope": "read edit run"}
        claims.update(changes or {})
        for claim in missing:
            claims.pop(claim, None)
        return jwt.encode(claims, key or self.private_key, algorithm="RS256", headers={"kid": kid, **(extra_headers or {})})

    async def test_valid_signature_issuer_audience_scopes_and_principal(self):
        access = await self.verifier.verify_token(self.signed())
        self.assertEqual(access.subject, "test-owner")
        self.assertEqual(access.client_id, "web-client")
        self.assertEqual(access.scopes, ["read", "edit", "run"])
        self.assertEqual(access.resource, AUDIENCE)
        self.assertEqual(access.claims, {"iss": ISSUER})
        self.assertEqual(str(self.requests[0].url), JWKS)
        self.assertNotIn("authorization", self.requests[0].headers)

    async def test_bad_claims_and_signatures_fail_closed(self):
        cases = [
            self.signed({"iss": "https://different.example.test/"}),
            self.signed({"aud": "https://different.example.test/mcp"}),
            self.signed({"exp": int(time.time()) - 1}),
            self.signed({"exp": "9999999999"}),
            self.signed({"exp": float("inf")}),
            self.signed({"nbf": int(time.time()) + 300}),
            self.signed({"sub": ""}),
            self.signed({"client_id": 12}),
            self.signed({"scope": "run"}),
            self.signed({"scope": ""}),
            self.signed({"scope": {"read": True}}),
            self.signed(key=self.other_key),
            *(self.signed(missing=[claim]) for claim in ("iss", "aud", "exp", "sub", "scope")),
        ]
        for index, token in enumerate(cases):
            with self.subTest(case=index):
                self.assertIsNone(await self.verifier.verify_token(token))

    async def test_algorithms_and_malformed_tokens_are_rejected_before_network(self):
        cases = ["bad-token", "x" * 32769,
                 jwt.encode({"sub": "test-owner"}, secrets.token_urlsafe(32), algorithm="HS256", headers={"kid": "current"}),
                 jwt.encode({"sub": "test-owner"}, None, algorithm="none", headers={"kid": "current"}),
                 self.signed(extra_headers={"crit": ["unsupported"], "unsupported": True})]
        for index, token in enumerate(cases):
            with self.subTest(case=index):
                self.assertIsNone(await self.verifier.verify_token(token))
        self.assertEqual(len(self.requests), 0)

    async def test_configured_scope_claim_can_be_an_array(self):
        verifier = OAuthTokenVerifier(ISSUER, AUDIENCE, JWKS, scope_claim="scp", client=self.client)
        access = await verifier.verify_token(self.signed({"scp": ["read", "edit", "read"]}, missing=["scope"]))
        self.assertEqual(access.scopes, ["read", "edit"])
        self.assertIsNone(await verifier.verify_token(self.signed({"scp": ["read edit"]})))

    async def test_only_the_configured_owner_subject_can_connect(self):
        verifier = OAuthTokenVerifier(ISSUER, AUDIENCE, JWKS, allowed_subjects=["test-owner"], client=self.client)
        self.assertIsNotNone(await verifier.verify_token(self.signed()))
        self.assertIsNone(await verifier.verify_token(self.signed({"sub": "another-valid-issuer-user"})))
        self.assertIsNone(await verifier.verify_token(self.signed({"sub": "Test-Owner"})))

    async def test_token_cannot_choose_a_jwks_endpoint(self):
        access = await self.verifier.verify_token(self.signed(extra_headers={"jku": "https://attacker.example.test/keys"}))
        self.assertIsNotNone(access)
        self.assertEqual([str(request.url) for request in self.requests], [JWKS])

    async def test_cache_and_unknown_key_rotation_are_bounded(self):
        self.assertIsNotNone(await self.verifier.verify_token(self.signed()))
        self.assertIsNotNone(await self.verifier.verify_token(self.signed()))
        self.document = {"keys": [self.jwk(self.other_key, "rotated")]}
        rotated = self.signed(key=self.other_key, kid="rotated")
        self.assertIsNone(await self.verifier.verify_token(rotated))
        self.assertEqual(len(self.requests), 1)
        self.clock += 31
        self.assertIsNotNone(await self.verifier.verify_token(rotated))
        self.assertEqual(len(self.requests), 2)
        for index in range(5):
            self.assertIsNone(await self.verifier.verify_token(self.signed(kid=f"unknown-{index}")))
        self.assertEqual(len(self.requests), 2)

    async def test_expired_key_cache_is_not_used_after_issuer_failure(self):
        self.assertIsNotNone(await self.verifier.verify_token(self.signed()))
        self.clock += 301
        self.status_code = 503
        self.assertIsNone(await self.verifier.verify_token(self.signed()))
        self.assertIsNone(await self.verifier.verify_token(self.signed()))
        self.assertEqual(len(self.requests), 2)

    async def test_redirects_are_not_followed(self):
        self.status_code = 302
        self.headers = {"location": "https://other.example.test/keys"}
        self.assertIsNone(await self.verifier.verify_token(self.signed()))
        self.assertEqual(len(self.requests), 1)

    async def test_invalid_ambiguous_and_oversized_jwks_fail_closed(self):
        key = self.jwk(self.private_key, "current")
        documents = [[], {"keys": []}, {"keys": [key, key]},
                     {"keys": [dict(key, use="enc")]},
                     {"keys": [dict(key, alg="RS512")]},
                     {"keys": [dict(key, key_ops=["sign"])]},
                     {"keys": [dict(key, n="invalid")]},
                     {"keys": [key], "padding": "x" * 1_048_577}]
        for index, document in enumerate(documents):
            with self.subTest(case=index):
                self.document = document
                verifier = OAuthTokenVerifier(ISSUER, AUDIENCE, JWKS, client=self.client)
                self.assertIsNone(await verifier.verify_token(self.signed()))

    async def test_http_failure_is_an_authentication_failure(self):
        def fail(request):
            raise httpx.ConnectError("Issuer unavailable", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            verifier = OAuthTokenVerifier(ISSUER, AUDIENCE, JWKS, client=client)
            self.assertIsNone(await verifier.verify_token(self.signed()))

    async def test_configuration_refuses_insecure_endpoints_and_algorithms(self):
        cases = [
            {"issuer_url": "http://issuer.example.test"},
            {"jwks_url": "http://issuer.example.test/keys"},
            {"jwks_url": "https://user:password@issuer.example.test/keys"},
            {"jwks_url": "https://issuer.example.test/keys#fragment"},
            {"jwks_url": "https://issuer.example.test:99999/keys"},
            {"audience": ""}, {"allowed_algorithms": ("HS256",)},
            {"allowed_algorithms": ()}, {"jwks_ttl_seconds": 3601},
            {"allowed_subjects": [""]}, {"allowed_subjects": "test-owner"},
        ]
        for index, change in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                OAuthTokenVerifier(**{"issuer_url": ISSUER, "audience": AUDIENCE, "jwks_url": JWKS,
                                       "client": self.client, **change})


class ScopeTests(unittest.TestCase):
    def test_scope_is_taken_from_the_authenticated_request(self):
        access = AccessToken(token=secrets.token_urlsafe(32), client_id="test-client", scopes=["read"])
        context = auth_context_var.set(AuthenticatedUser(access))
        try:
            self.assertIs(require_scope("read"), access)
            with self.assertRaisesRegex(PermissionError, "edit scope"):
                require_scope("edit")
        finally:
            auth_context_var.reset(context)
        with self.assertRaisesRegex(PermissionError, "Authentication"):
            require_scope("read")


if __name__ == "__main__":
    unittest.main()
