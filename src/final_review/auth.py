"""Database-backed password authentication and opaque browser sessions."""

import ipaddress
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request, Response
from psycopg.rows import dict_row

from .config import Settings


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None = None
    is_local: bool = False


class DatabaseAuth:
    """Own users and sessions; the browser only ever receives an opaque ID."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.database_url = settings.database_url.get_secret_value()
        self.connection = None
        self.passwords = PasswordHasher()

    @property
    def enabled(self) -> bool:
        return self.settings.auth_mode == "database" and bool(self.database_url)

    def setup(self):
        if not self.enabled:
            return
        try:
            self.connection = psycopg.connect(self.database_url, row_factory=dict_row)
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('public.app_users') AS table_name")
                if cursor.fetchone()["table_name"] is None:
                    raise RuntimeError("missing app_users")
                cursor.execute("SELECT to_regclass('public.auth_sessions') AS table_name")
                if cursor.fetchone()["table_name"] is None:
                    raise RuntimeError("missing auth_sessions")
        except (psycopg.Error, RuntimeError) as exc:
            self.close()
            raise RuntimeError(
                "认证数据库未初始化；请先执行 db/migrations/001_database_auth.sql"
            ) from exc

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _connection(self):
        if self.connection is None:
            raise HTTPException(503, "认证服务暂时不可用")
        return self.connection

    @staticmethod
    def _session_hash(session_id: str) -> str:
        return sha256(session_id.encode("ascii")).hexdigest()

    @staticmethod
    def _email(email: str) -> str:
        return email.strip().lower()

    def _new_session(self, user: CurrentUser) -> str:
        session_id = secrets.token_urlsafe(48)
        expires_at = datetime.now(UTC) + timedelta(days=self.settings.auth_session_days)
        try:
            with self._connection().cursor() as cursor:
                cursor.execute(
                    "INSERT INTO auth_sessions (session_hash, user_id, expires_at) "
                    "VALUES (%s, %s, %s)",
                    (self._session_hash(session_id), user.id, expires_at),
                )
            self._connection().commit()
        except psycopg.Error as exc:
            self._connection().rollback()
            raise HTTPException(503, "认证服务暂时不可用") from exc
        return session_id

    def sign_up(self, email: str, password: str) -> tuple[CurrentUser, str]:
        user = CurrentUser(id=str(uuid4()), email=self._email(email))
        try:
            password_hash = self.passwords.hash(password)
            with self._connection().cursor() as cursor:
                cursor.execute(
                    "INSERT INTO app_users (id, email, password_hash) VALUES (%s, %s, %s)",
                    (user.id, user.email, password_hash),
                )
            self._connection().commit()
        except psycopg.errors.UniqueViolation as exc:
            self._connection().rollback()
            raise HTTPException(409, "该邮箱已注册，请直接登录") from exc
        except psycopg.Error as exc:
            self._connection().rollback()
            raise HTTPException(503, "认证服务暂时不可用") from exc
        return user, self._new_session(user)

    def sign_in(
        self, email: str, password: str, remote_addr: str | None
    ) -> tuple[CurrentUser, str]:
        normalized_email = self._email(email)
        try:
            remote_addr = str(ipaddress.ip_address(remote_addr)) if remote_addr else None
        except ValueError:
            remote_addr = None
        try:
            with self._connection().cursor() as cursor:
                cursor.execute(
                    """SELECT count(*) AS failures FROM auth_login_attempts
                    WHERE email = %s AND ip_address IS NOT DISTINCT FROM %s
                      AND successful = false AND created_at > now() - interval '15 minutes'""",
                    (normalized_email, remote_addr),
                )
                if cursor.fetchone()["failures"] >= 5:
                    raise HTTPException(429, "登录尝试过于频繁，请稍后再试")
                cursor.execute(
                    "SELECT id, email, password_hash FROM app_users WHERE email = %s",
                    (normalized_email,),
                )
                row = cursor.fetchone()
            valid = bool(row)
            if row:
                try:
                    valid = self.passwords.verify(row["password_hash"], password)
                except (VerifyMismatchError, VerificationError, InvalidHashError):
                    valid = False
            with self._connection().cursor() as cursor:
                cursor.execute(
                    "INSERT INTO auth_login_attempts (email, ip_address, successful) "
                    "VALUES (%s, %s, %s)",
                    (normalized_email, remote_addr, valid),
                )
            self._connection().commit()
        except HTTPException:
            self._connection().rollback()
            raise
        except psycopg.Error as exc:
            self._connection().rollback()
            raise HTTPException(503, "认证服务暂时不可用") from exc
        if not valid:
            raise HTTPException(401, "邮箱或密码不正确")
        user = CurrentUser(id=str(row["id"]), email=row["email"])
        return user, self._new_session(user)

    def current_user(self, request: Request) -> CurrentUser:
        session_id = request.cookies.get(self.settings.auth_cookie_name)
        if not session_id:
            raise HTTPException(401, "请先登录")
        try:
            with self._connection().cursor() as cursor:
                cursor.execute(
                    """SELECT u.id, u.email FROM auth_sessions s
                    JOIN app_users u ON u.id = s.user_id
                    WHERE s.session_hash = %s AND s.revoked_at IS NULL
                      AND s.expires_at > now()""",
                    (self._session_hash(session_id),),
                )
                row = cursor.fetchone()
        except psycopg.Error as exc:
            raise HTTPException(503, "认证服务暂时不可用") from exc
        if row is None:
            raise HTTPException(401, "登录已失效，请重新登录")
        return CurrentUser(id=str(row["id"]), email=row["email"])

    def revoke(self, request: Request):
        session_id = request.cookies.get(self.settings.auth_cookie_name)
        if not session_id or self.connection is None:
            return
        try:
            with self._connection().cursor() as cursor:
                cursor.execute(
                    "UPDATE auth_sessions SET revoked_at = now() "
                    "WHERE session_hash = %s AND revoked_at IS NULL",
                    (self._session_hash(session_id),),
                )
            self._connection().commit()
        except psycopg.Error:
            self._connection().rollback()

    def set_session_cookie(self, response: Response, session_id: str):
        response.set_cookie(
            self.settings.auth_cookie_name,
            session_id,
            max_age=self.settings.auth_session_days * 86400,
            httponly=True,
            secure=self.settings.auth_cookie_secure,
            samesite="lax",
            domain=self.settings.auth_cookie_domain or None,
            path="/",
        )

    def clear_session_cookie(self, response: Response):
        response.delete_cookie(
            self.settings.auth_cookie_name,
            domain=self.settings.auth_cookie_domain or None,
            path="/",
        )
