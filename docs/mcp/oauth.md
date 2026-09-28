# OAuth sign-in

OAuth is optional. Arkennemasis can verify tokens from an external identity provider and block individual OAuth clients locally. It does not create an identity-provider account or run its own login, consent or token-issuing service. Your existing private-URL connection continues working until you explicitly change its mode and restart the gateway.

## Prepare your provider

Use an established OAuth provider with a public HTTPS issuer. It must publish authorization-server discovery, provide authorization-code login with PKCE `S256`, and support the client registration method you select: a predefined client, dynamic registration, or Client ID Metadata Documents. Copy the exact redirect URI displayed by your AI connector into the provider's allowlist; do not guess it. Configure resource binding so access tokens have your public `/mcp` endpoint as their audience. The provider handles consent, refresh tokens and provider-side grant revocation. See the [MCP authorization specification](https://modelcontextprotocol.io/specification/latest/basic/authorization) and [OpenAI's provider requirements](https://developers.openai.com/plugins/build/auth).

This gateway specifically accepts **asymmetrically signed JWT access tokens** verified against your configured JWKS endpoint. Opaque tokens, ID tokens intended for another audience, shared-secret signing (`HS256`), or arbitrary token introspection are not supported.

The JWT must include:

| Claim | Required value |
| --- | --- |
| `iss` | Exactly the configured issuer, including any trailing slash |
| `aud` | The public HTTPS origin followed by `/mcp` |
| `exp` | An unexpired numeric expiry |
| `sub` | One of your explicitly allowed provider user IDs |
| `scope` or configured scope claim | A space-separated scope string or array of scope names |
| `client_id` or `azp` | The provider-signed OAuth client identifier; if both exist they must agree |

The signing header needs a supported algorithm and a `kid` matching one unambiguous provider signing key. The gateway never uses token-supplied `jku` URLs. It fetches only the owner-configured JWKS endpoint, without following redirects, and bounds its key cache and response size.

## Configure it locally

1. Complete **MCP setup** first, including a fixed public HTTPS address and the permissions you want to allow.
2. Open **OAuth sign-in** from local **MCP setup**. This screen is available only from the loopback ComfyUI page.
3. Enter the public origin, exact provider issuer, JWKS URL, allowed user IDs, signing algorithms and scope-claim name. The audience is the public origin plus `/mcp`. Provider user IDs are `sub` values, not necessarily email addresses.
4. Choose **Save provider draft**. This validates and saves a private local draft. It does not contact your provider, change the connection mode, or prove that provider login works.
5. After configuring your provider and reviewing the saved draft, choose **Enable OAuth after restart**. Restart the AI connection to apply the mode. If using the portable with-MCP BAT, close and relaunch that same `*_with_mcp.bat` when no job is running. The ordinary BAT does not start the gateway.
6. In your AI connector, use `https://YOUR-PUBLIC-HOST/mcp`, select **OAuth**, and follow the provider's login and consent flow. The old `/connect/PRIVATE-VALUE/mcp` URL is no longer accepted in OAuth mode.

The public protected-resource metadata is available at `/.well-known/oauth-protected-resource/mcp`, with an origin-level alias at `/.well-known/oauth-protected-resource`. It advertises the issuer and installation-enabled capabilities. Provider discovery and login endpoints are hosted by your identity provider, not by Arkennemasis.

OAuth permission is the intersection of the token's granted scopes and the installation's enabled scopes. For example, a token carrying `comfy:develop` still cannot edit node code until the local owner enables development and allows that specific node-pack folder. A read-only client can receive only `comfy:read`; there is no automatic expansion to all installation permissions.

## Block a client

In **OAuth sign-in**, enter the exact `client_id` or `azp` from your provider and choose **Block client**. The gateway rechecks this local denylist on every authenticated request, so an already-issued token stops working on its next request. Blocks survive gateway restarts. Requests already executing are not cancelled retroactively.

Blocking a client ID blocks that entire OAuth client; it is not a per-device or per-login-session control. A shared CIMD client ID may represent several connections. **Unblock** removes only the local block; the token still needs a valid signature, issuer, audience, subject and scopes. Revoke the grant at your provider as well if you want to stop future token issuance there.

Local client blocks are stored in `mcp_service/.local/oauth-clients.json` under the configured state directory. Invalid or unreadable revocation state causes OAuth authentication to fail closed. Repair a corrupt file locally; the owner UI deliberately cannot silently clear a broken denylist. No access or refresh tokens are stored by this feature.

## Switch back or troubleshoot

**Restore private-URL mode** explicitly selects the existing private connection URL again. It preserves the original local, bridge and connection credentials. Restart the AI connection, then configure your connector for that private URL with **No Auth**. OAuth settings and client blocks remain saved for later reuse.

| Problem | Check |
| --- | --- |
| Enable is unavailable | Save a valid draft; review any local configuration errors. Editing the displayed provider values requires saving them again. |
| Discovery cannot be reached | Test the fixed public hostname and tunnel. OAuth cannot repair DNS, TLS or tunnel reachability. |
| Login fails before a token is issued | Check provider discovery, chosen client registration method, exact redirect URI and PKCE support. |
| Requests return 401 | Check the token's issuer, audience, expiry, owner subject, client identity, signing key and local client blocks. |
| A tool reports insufficient permission | Check both the token scope and the installation capability; check the allowed node pack for source operations. |
| Mode appears unchanged | The screen shows saved configuration. Restart the gateway to apply mode or provider changes. Client revocations apply without restart. |

Automated tests cover token validation, key rotation and failure handling, persisted client blocks, exact owner-only routes, staging, revision checks and atomic configuration replacement. A real provider login must still be verified for your own provider and AI connector; passing these tests does not establish that external integration.
