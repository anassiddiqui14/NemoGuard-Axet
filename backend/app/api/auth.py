import os
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from fastapi import Request, HTTPException, Depends
from app.store.database_factory import get_database, get_db_backend_name
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from pydantic import BaseModel


def serialize_roles(roles: List[str], db=None) -> object:
    """
    Serializes a Python list[str] of roles into whatever shape the
    TARGET backend's `platform_user.roles` column expects:
      - Postgres: a real TEXT[] array -- psycopg2 accepts a Python list
        directly for an ARRAY column, so no transformation is needed.
      - SQLite: a TEXT column containing a JSON array (see
        schema_sqlite.sql's platform_user.roles DEFAULT '["viewer"]').

    The target backend is determined, in order:
      1. From the concrete type of `db` (a SqliteDatabase/PostgresDatabase
         instance) if provided -- this is authoritative and avoids any
         risk of disagreeing with `NEMOGUARD_DB_BACKEND` in edge cases
         (e.g. tests, or a caller holding a db instance constructed
         before an env var change).
      2. Falling back to get_db_backend_name() (the env var) only when
         no `db` instance is available to inspect.

    This is the ONLY place roles are serialized for a DB write; every
    other part of the app (RBAC checks, JWT claims, API responses)
    continues to work with a plain list[str].
    """
    is_sqlite = (
        db.__class__.__name__ == "SqliteDatabase" if db is not None
        else get_db_backend_name() == "sqlite"
    )
    if is_sqlite:
        return json.dumps(list(roles))
    return list(roles)


def deserialize_roles(raw_roles) -> List[str]:
    """
    Converts whatever `platform_user.roles` came back as from the
    CURRENT backend into a plain Python list[str]:
      - Postgres: psycopg2 already returns a real Python list for a
        TEXT[] column -- pass through unchanged.
      - SQLite: the column is a JSON-encoded TEXT string -- parse it.
        Guards against None/empty/already-a-list values defensively so
        a malformed or legacy row never raises here.
    """
    if raw_roles is None:
        return ["viewer"]
    if isinstance(raw_roles, list):
        return raw_roles
    if isinstance(raw_roles, str):
        try:
            parsed = json.loads(raw_roles)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return ["viewer"]

SECRET_KEY = os.environ.get("JWT_SECRET")
if not SECRET_KEY:
    raise RuntimeError(
        "JWT_SECRET environment variable is not set. Refusing to start with an insecure default. "
        "Set JWT_SECRET in your .env file (see .env.example)."
    )
ALGORITHM = "HS256"
IS_DEV_ENV = os.environ.get("ENV", "production").lower() in ("development", "dev", "local")

AXET_FLOWS_AUTH = os.environ.get("AXET_FLOWS_AUTH", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

security = HTTPBearer(auto_error=False)

class User(BaseModel):
    user_id: str
    email: str
    roles: List[str]
    tenant_id: str
    workspace_id: str

class LoginRequest(BaseModel):
    email: str
    password: str

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def _decode_axet_identity(token: str) -> tuple[str, str]:
    """
    Decode the aXet/Okta bearer identity.

    In aXet Flows, authentication is enforced by the platform middleware.
    The application consumes the already-authenticated JWT and resolves its
    Okta identity against NemoGuard's own platform_user table.
    """
    try:
        payload = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
            },
        )
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid aXet authentication token")

    okta_id = str(payload.get("okta_id") or payload.get("sub") or "").strip()
    email = str(payload.get("email") or payload.get("login") or "").strip().lower()

    if not okta_id:
        raise HTTPException(
            status_code=401,
            detail="aXet token does not contain an Okta identity",
        )

    return okta_id, email


def _get_axet_user(token: str) -> User:
    okta_id, email = _decode_axet_identity(token)

    db = get_database()

    with db.get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT
                user_id,
                email,
                roles,
                tenant_id,
                workspace_id,
                is_active,
                okta_id
            FROM platform_user
            WHERE okta_id = %s
            """,
            (okta_id,),
        )
        row = cursor.fetchone()

        # First aXet login for an existing NemoGuard account:
        # safely link the corporate Okta identity using the trusted email claim.
        if row is None and email:
            cursor = conn.execute(
                """
                SELECT
                    user_id,
                    email,
                    roles,
                    tenant_id,
                    workspace_id,
                    is_active,
                    okta_id
                FROM platform_user
                WHERE LOWER(email) = LOWER(%s)
                """,
                (email,),
            )
            row = cursor.fetchone()

            if row is not None:
                existing_okta_id = row[6]

                if existing_okta_id and existing_okta_id != okta_id:
                    raise HTTPException(
                        status_code=403,
                        detail="This NemoGuard account is linked to a different Okta identity",
                    )

                if not existing_okta_id:
                    conn.execute(
                        """
                        UPDATE platform_user
                        SET okta_id = %s
                        WHERE user_id = %s
                        """,
                        (okta_id, row[0]),
                    )

        if row is None:
            raise HTTPException(
                status_code=403,
                detail="Authenticated aXet user is not provisioned in NemoGuard",
            )

        user_id, user_email, roles, tenant_id, workspace_id, is_active, _ = row

        if not is_active:
            raise HTTPException(
                status_code=403,
                detail="NemoGuard account is inactive",
            )

        conn.execute(
            """
            UPDATE platform_user
            SET last_login_at = %s
            WHERE user_id = %s
            """,
            (datetime.now(timezone.utc).isoformat(), user_id),
        )

    return User(
        user_id=user_id,
        email=user_email,
        roles=deserialize_roles(roles),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )

def authenticate_token(token: str) -> User:
    """
    Authenticate a raw bearer token.

    Used by normal Authorization-header requests and by transports such as
    EventSource/SSE that cannot send custom Authorization headers.
    """

    # aXet Flows authentication.
    if AXET_FLOWS_AUTH:
        return _get_axet_user(token)

    # Existing standalone/local NemoGuard authentication.
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=401,
                detail="Invalid authentication credentials",
            )

        return User(
            user_id=user_id,
            email=payload.get("email", ""),
            roles=payload.get("roles", ["viewer"]),
            tenant_id=payload.get("tenant_id", "default_tenant"),
            workspace_id=payload.get("workspace_id", "default_workspace"),
        )

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    token: Optional[str] = None

    # Standard/local NemoGuard authentication.
    if credentials and credentials.credentials:
        token = credentials.credentials

    # aXet Flows runtime stores the authenticated JWT in an HttpOnly cookie.
    # The python-gateway forwards that cookie unchanged to FastAPI.
    elif AXET_FLOWS_AUTH:
        token = request.cookies.get("axetflows_token")

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )

    return authenticate_token(token)

def require_role(required_role: str):
    """
    Dependency generator for RBAC.
    """
    async def role_checker(current_user: User = Depends(get_current_user)):
        if "admin" in current_user.roles:
            return current_user # Admins can access everything
        if required_role not in current_user.roles:
            raise HTTPException(status_code=403, detail="Not enough permissions")
        return current_user
    return role_checker

# Mock Endpoint to generate a test token — only reachable when ENV is
# development/dev/local (enforced in main.py). Never usable in production.
def get_mock_token(role: str = "admin"):
    access_token = create_access_token(
        data={
            "sub": "mock-user-123",
            "email": "test@nemoguard.com",
            "roles": [role],
            "tenant_id": "tenant_A",
            "workspace_id": "ws_alpha"
        },
        expires_delta=timedelta(days=1)
    )
    return access_token


# ---------------------------------------------------------------------------
# Real credential-backed authentication (production login path).
#
# Passwords are never stored in plaintext. We hash with PBKDF2-HMAC-SHA256
# (via hashlib, no extra dependency needed) using a random per-user salt and
# a high iteration count, in the standard `pbkdf2_sha256$<iterations>$<salt>$<hash>`
# format so it's self-describing and can be re-parameterized later without a
# migration.
# ---------------------------------------------------------------------------

_PBKDF2_ITERATIONS = 260_000


def hash_password(plain_password: str) -> str:
    salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256", plain_password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${derived.hex()}"


def verify_password(plain_password: str, stored_hash: str) -> bool:
    try:
        algo, iterations_str, salt, hex_digest = stored_hash.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iterations_str)
    except (ValueError, AttributeError):
        return False
    derived = hashlib.pbkdf2_hmac(
        "sha256", plain_password.encode("utf-8"), salt.encode("utf-8"), iterations
    )
    return hmac.compare_digest(derived.hex(), hex_digest)


def issue_token_for_user(user_row: dict) -> str:
    """Builds a real, short-lived JWT for an authenticated platform_user row."""
    return create_access_token(
        data={
            "sub": user_row["user_id"],
            "email": user_row["email"],
            "roles": list(user_row.get("roles") or ["viewer"]),
            "tenant_id": user_row.get("tenant_id", "default_tenant"),
            "workspace_id": user_row.get("workspace_id", "default_workspace"),
        },
        expires_delta=timedelta(hours=8),
    )
