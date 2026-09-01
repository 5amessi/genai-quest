from __future__ import annotations

import asyncio
from typing import Protocol

from app.domain.errors import AuthenticationError
from app.domain.models import Principal


class Authenticator(Protocol):
    async def authenticate(self, token: str) -> Principal: ...


DEMO_IDENTITIES = {
    "demo-engineer": Principal(
        subject="alice.engineer",
        groups=frozenset({"company-all", "engineering"}),
    ),
    "demo-hr": Principal(
        subject="henry.hr",
        groups=frozenset({"company-all", "hr"}),
    ),
    "demo-finance": Principal(
        subject="fatima.finance",
        groups=frozenset({"company-all", "finance"}),
    ),
    "demo-admin": Principal(
        subject="ada.admin",
        groups=frozenset({"company-all", "engineering", "hr", "finance", "legal"}),
        roles=frozenset({"knowledge.ingest", "knowledge.debug"}),
    ),
}


class DemoAuthenticator:
    """Explicit local-only identities. Settings reject this mode in production."""

    async def authenticate(self, token: str) -> Principal:
        principal = DEMO_IDENTITIES.get(token)
        if principal is None:
            raise AuthenticationError("invalid demo bearer token")
        return principal


class EntraJwtAuthenticator:
    """Validate Entra JWT signature, issuer, audience, expiry and entitlement claims."""

    def __init__(self, *, tenant_id: str, audience: str, issuer: str | None = None) -> None:
        self.tenant_id = tenant_id
        self.audience = audience
        self.issuer = issuer or f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self.jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"

    async def authenticate(self, token: str) -> Principal:
        try:
            import jwt
        except ImportError as exc:  # pragma: no cover - exercised only with Azure extra
            raise RuntimeError("install the 'azure' dependency extra for Entra auth") from exc

        def decode() -> dict[str, object]:
            jwk_client = jwt.PyJWKClient(self.jwks_url, cache_keys=True, lifespan=3600)
            signing_key = jwk_client.get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )

        try:
            claims = await asyncio.to_thread(decode)
        except Exception as exc:
            raise AuthenticationError("invalid Entra bearer token") from exc

        # Group overage must be resolved by a trusted gateway/Graph enrichment flow;
        # silently treating it as no groups can produce confusing access behavior.
        if "_claim_names" in claims:
            raise AuthenticationError("group overage token is unsupported by this deployment")
        groups = frozenset(str(value) for value in claims.get("groups", []))
        roles = frozenset(str(value) for value in claims.get("roles", []))
        subject = str(claims.get("oid") or claims["sub"])
        return Principal(subject=subject, groups=groups, roles=roles)


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise AuthenticationError("missing Authorization header")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError("Authorization must use Bearer authentication")
    return token.strip()
