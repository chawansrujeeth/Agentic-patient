# db.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from requests import RequestException
from dotenv import load_dotenv

_ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(_ENV_PATH)


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    anon_key: Optional[str]
    service_role_key: str
    timeout_s: float = 10.0


class SupabaseError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None, response_text: Optional[str] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text

    def __str__(self) -> str:
        base = super().__str__()
        details = []
        if self.status_code is not None:
            details.append(f"status={self.status_code}")
        if self.response_text:
            details.append(f"response={self.response_text}")
        if not details:
            return base
        return f"{base} ({', '.join(details)})"


class SupabaseClient:
    def __init__(self, cfg: SupabaseConfig) -> None:
        self._cfg = cfg
        self._base_url = f"{cfg.url.rstrip('/')}/rest/v1"
        self._session = requests.Session()

    def _headers(self, prefer: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "apikey": self._cfg.service_role_key,
            "Authorization": f"Bearer {self._cfg.service_role_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, str]] = None,
        json_body: Optional[Any] = None,
        prefer: Optional[str] = None,
        return_headers: bool = False,
    ) -> Any:
        url = f"{self._base_url}/{path.lstrip('/')}" if path else self._base_url
        try:
            resp = self._session.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=self._headers(prefer),
                timeout=self._cfg.timeout_s,
            )
        except RequestException as exc:
            raise SupabaseError(
                f"Supabase request failed ({method} {url})",
                response_text=str(exc),
            ) from exc
        if not resp.ok:
            raise SupabaseError(
                f"Supabase request failed ({method} {url})",
                status_code=resp.status_code,
                response_text=resp.text,
            )
        if not resp.text:
            return (None, dict(resp.headers)) if return_headers else None
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        return (body, dict(resp.headers)) if return_headers else body

    def table(self, name: str) -> "SupabaseTable":
        return SupabaseTable(self, name)

    def rpc(self, fn: str, args: Dict[str, Any]) -> Any:
        return self.request("POST", f"rpc/{fn}", json_body=args) or []


class SupabaseTable:
    def __init__(self, client: SupabaseClient, name: str) -> None:
        self._client = client
        self._name = name

    def select(
        self,
        *,
        filters: Optional[Dict[str, Any]] = None,
        columns: str = "*",
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[Any] = None,
        raw_params: Optional[Dict[str, str]] = None,
    ) -> Any:
        params: Dict[str, str] = {"select": columns}
        _apply_filters(params, filters)
        if order:
            if isinstance(order, (tuple, list)) and len(order) == 2:
                col, direction = order
                params["order"] = f"{col}.{direction}"
            else:
                params["order"] = str(order)
        if limit is not None:
            params["limit"] = str(int(limit))
        if offset is not None:
            params["offset"] = str(int(offset))
        if raw_params:
            params.update({str(k): str(v) for k, v in raw_params.items()})
        return self._client.request("GET", self._name, params=params) or []

    def select_with_count(
        self,
        *,
        filters: Optional[Dict[str, Any]] = None,
        columns: str = "*",
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order: Optional[Any] = None,
        raw_params: Optional[Dict[str, str]] = None,
    ) -> Any:
        """
        Returns (rows, total_count) when PostgREST provides Content-Range.
        """
        params: Dict[str, str] = {"select": columns}
        _apply_filters(params, filters)
        if order:
            if isinstance(order, (tuple, list)) and len(order) == 2:
                col, direction = order
                params["order"] = f"{col}.{direction}"
            else:
                params["order"] = str(order)
        if limit is not None:
            params["limit"] = str(int(limit))
        if offset is not None:
            params["offset"] = str(int(offset))
        if raw_params:
            params.update({str(k): str(v) for k, v in raw_params.items()})

        body, headers = self._client.request(
            "GET",
            self._name,
            params=params,
            prefer="count=exact",
            return_headers=True,
        )
        rows = body or []
        total = None
        content_range = (headers or {}).get("Content-Range") or (headers or {}).get("content-range")
        if isinstance(content_range, str) and "/" in content_range:
            total_str = content_range.split("/")[-1].strip()
            if total_str.isdigit():
                total = int(total_str)
        if total is None:
            total = len(rows) if isinstance(rows, list) else 0
        return rows, total

    def insert(self, rows: Any, *, returning: bool = True) -> Any:
        payload = rows if isinstance(rows, list) else [rows]
        prefer = "return=representation" if returning else "return=minimal"
        return self._client.request("POST", self._name, json_body=payload, prefer=prefer) or []

    def update(self, values: Dict[str, Any], *, filters: Dict[str, Any], returning: bool = False) -> Any:
        params: Dict[str, str] = {}
        _apply_filters(params, filters)
        prefer = "return=representation" if returning else "return=minimal"
        return self._client.request("PATCH", self._name, params=params, json_body=values, prefer=prefer) or []


_client: Optional[SupabaseClient] = None


def _format_filter_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def _apply_filters(params: Dict[str, str], filters: Optional[Dict[str, Any]]) -> None:
    if not filters:
        return
    for key, value in filters.items():
        if isinstance(value, tuple) and len(value) == 2:
            op, raw_val = value
            if op == "in":
                if isinstance(raw_val, (list, tuple)):
                    joined = ",".join(_format_filter_value(v) for v in raw_val)
                else:
                    joined = _format_filter_value(raw_val)
                params[key] = f"in.({joined})"
            else:
                params[key] = f"{op}.{_format_filter_value(raw_val)}"
        else:
            params[key] = f"eq.{_format_filter_value(value)}"


def _read_env(*keys: str) -> Optional[str]:
    for key in keys:
        val = os.getenv(key)
        if val:
            return val.strip()
    return None


def _load_config() -> SupabaseConfig:
    url = _read_env("SUPABASE_URL", "Supabase_URL", "Suppabase_URL", "supabase_url", "suppabase_url")
    anon_key = _read_env(
        "SUPABASE_ANON_KEY",
        "SUPABASE_ANONKEY",
        "SUPABASE_ANON",
        "supabase_anon_key",
        "suppabase_anonkey",
    )
    service_role_key = _read_env(
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_SERVICE_ROLE",
        "supabase_service_role",
        "suppabase_service_role",
    )

    if not url:
        raise RuntimeError("Missing required env var: SUPABASE_URL")
    if not service_role_key:
        raise RuntimeError("Missing required env var: SUPABASE_SERVICE_ROLE_KEY")

    timeout_s_val = _read_env("SUPABASE_TIMEOUT_S")
    timeout_s = 10.0
    if timeout_s_val:
        try:
            timeout_s = float(timeout_s_val)
        except ValueError:
            timeout_s = 10.0

    return SupabaseConfig(url=url, anon_key=anon_key, service_role_key=service_role_key, timeout_s=timeout_s)


def get_db() -> SupabaseClient:
    global _client
    if _client is not None:
        return _client

    cfg = _load_config()
    _client = SupabaseClient(cfg)
    return _client


def ping() -> bool:
    """
    Fast health check: returns True if Supabase responds.
    Raises exception if it cannot connect.
    """
    client = get_db()
    try:
        client.table("cases").select(columns="case_id", limit=1)
    except SupabaseError as exc:
        text = (exc.response_text or "").lower()
        if exc.status_code == 404 and ("pgrst205" in text or "could not find the table" in text):
            raise RuntimeError(
                "Supabase schema is missing required tables. "
                "Run backend/scripts/supabase_schema.sql in the Supabase SQL editor, "
                "then re-run the app."
            ) from exc
        raise
    return True
