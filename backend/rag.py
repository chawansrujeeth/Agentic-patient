from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence

from openai import OpenAI

from ai_provider import is_gemini_provider
from db import SupabaseClient, get_db
from llm import QuotaExhaustedError

# Embedding calls go through whichever provider is configured (OpenAI or Gemini).
_EMBED_CLIENT: Optional[OpenAI] = None


@dataclass(frozen=True)
class _GeminiClient:
    mode: Literal["genai", "legacy"]
    client: Any


_GEMINI_SDK: Optional[_GeminiClient] = None
_GEMINI_SDKS: Dict[str, _GeminiClient] = {}


def _active_embed_model_name() -> str:
    """Return the embedding model name that matches the active provider."""

    if is_gemini_provider():
        return os.getenv("GEMINI_EMBED_MODEL") or os.getenv("EMBED_MODEL") or "text-embedding-004"
    return os.getenv("OPENAI_EMBED_MODEL") or os.getenv("EMBED_MODEL") or "text-embedding-3-small"


def _resolve_db(db: Optional[SupabaseClient]) -> SupabaseClient:
    return db if db is not None else get_db()


def _get_embedding_client() -> OpenAI:
    global _EMBED_CLIENT
    if _EMBED_CLIENT is not None:
        return _EMBED_CLIENT

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing required env var: OPENAI_API_KEY")

    kwargs: Dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url

    _EMBED_CLIENT = OpenAI(**kwargs)
    return _EMBED_CLIENT


def _split_env_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in re.split(r"[,\n;]+", raw) if item.strip()]


def _numbered_env_values(prefix: str, max_items: int = 10) -> List[str]:
    values: List[str] = []
    for idx in range(1, max_items + 1):
        val = os.getenv(f"{prefix}{idx}")
        if val and val.strip():
            values.append(val.strip())
    return values


def _dedupe_list(values: List[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _gemini_api_keys() -> List[str]:
    raw = os.getenv("GEMINI_API_KEYS") or os.getenv("GOOGLE_API_KEYS")
    keys = _split_env_list(raw)
    if keys:
        return keys
    base_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    keys = []
    if base_key and base_key.strip():
        keys.append(base_key.strip())
    keys.extend(_numbered_env_values("GEMINI_API_KEY"))
    keys.extend(_numbered_env_values("GOOGLE_API_KEY"))
    keys = _dedupe_list(keys)
    if keys:
        return keys
    raise RuntimeError(
        "Missing required env var: set GEMINI_API_KEY/GEMINI_API_KEYS or numbered GEMINI_API_KEY1.."
    )


def _gemini_api_key() -> str:
    return _gemini_api_keys()[0]


def _timeout_ms(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    if seconds <= 0:
        return None
    return int(seconds * 1000)


def _get_gemini_sdk(api_key: Optional[str] = None) -> _GeminiClient:
    """Lazy-load and configure the Gemini SDK (new or legacy)."""

    global _GEMINI_SDK
    key = api_key or _gemini_api_key()
    cached = _GEMINI_SDKS.get(key)
    if cached is not None:
        if cached.mode == "legacy":
            cached.client.configure(api_key=key)
        return cached
    if _GEMINI_SDK is not None and api_key is None:
        return _GEMINI_SDK

    try:
        from google import genai as genai_client
        from google.genai import types as genai_types
    except ImportError:
        genai_client = None
        genai_types = None

    if genai_client is not None and genai_types is not None:
        api_version = (os.getenv("GEMINI_API_VERSION") or "v1").strip() or "v1"
        timeout_val = _timeout_ms(os.getenv("GEMINI_EMBED_TIMEOUT_S") or os.getenv("GEMINI_TIMEOUT_S"))
        http_kwargs: Dict[str, Any] = {"api_version": api_version}
        if timeout_val is not None:
            http_kwargs["timeout"] = timeout_val
        http_options = genai_types.HttpOptions(**http_kwargs)
        client = genai_client.Client(api_key=key, http_options=http_options)
        sdk = _GeminiClient(mode="genai", client=client)
        _GEMINI_SDKS[key] = sdk
        if api_key is None:
            _GEMINI_SDK = sdk
        return sdk

    try:
        import google.generativeai as legacy_genai
    except ImportError as exc:  # pragma: no cover - import error surfaces at runtime
        raise RuntimeError(
            "Gemini support requires the google-genai package. "
            "Install it with: pip install google-genai"
        ) from exc

    legacy_genai.configure(api_key=key)
    sdk = _GeminiClient(mode="legacy", client=legacy_genai)
    _GEMINI_SDKS[key] = sdk
    if api_key is None:
        _GEMINI_SDK = sdk
    return sdk


def _is_quota_error(err: Exception) -> bool:
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    if isinstance(code, int) and code == 429:
        return True
    name = err.__class__.__name__.lower()
    if "resourceexhausted" in name or "toomanyrequests" in name or "ratelimit" in name:
        return True
    text = str(err).lower()
    return "resource_exhausted" in text or "quota" in text or "rate limit" in text or "429" in text


def _ensure_vector(seq: Sequence[float]) -> List[float]:
    vector = [float(x) for x in seq]
    if not vector:
        raise ValueError("Vector must contain at least one dimension")
    return vector


def _embed_with_openai(cleaned: str) -> List[float]:
    model_name = _active_embed_model_name()
    embed_client = _get_embedding_client()
    response = embed_client.embeddings.create(input=cleaned, model=model_name)
    if not response.data:
        raise RuntimeError("Embedding API returned no data")

    vec = response.data[0].embedding
    return _ensure_vector(vec)


def _embed_with_gemini(cleaned: str) -> List[float]:
    model_name = _active_embed_model_name()
    last_err: Optional[Exception] = None
    quota_only_failures = True

    for api_key in _gemini_api_keys():
        try:
            gemini_sdk = _get_gemini_sdk(api_key)
            if gemini_sdk.mode == "legacy":
                genai = gemini_sdk.client
                result = genai.embed_content(
                    model=model_name,
                    content=cleaned,
                    task_type="retrieval_document",
                )
                embedding: Optional[Iterable[float]] = None
                if isinstance(result, dict):
                    embedding = result.get("embedding")
                else:
                    embedding = getattr(result, "embedding", None)
                if embedding is None:
                    raise RuntimeError("Gemini embedding API returned no embedding vector")
                return _ensure_vector(list(embedding))

            client = gemini_sdk.client
            response = client.models.embed_content(model=model_name, contents=cleaned)
            embeddings = getattr(response, "embeddings", None)
            if not embeddings:
                raise RuntimeError("Gemini embedding API returned no embedding vector")
            first = embeddings[0]
            values = getattr(first, "values", None)
            if values is None and isinstance(first, dict):
                values = first.get("values")
            if values is None:
                raise RuntimeError("Gemini embedding API returned malformed embedding data")
            return _ensure_vector([float(x) for x in values])

        except Exception as exc:
            last_err = exc
            if _is_quota_error(exc):
                continue
            quota_only_failures = False
            break

    if quota_only_failures and last_err is not None:
        raise QuotaExhaustedError(
            "Gemini quota reached for all configured keys/models. Please come back tomorrow."
        ) from last_err
    raise RuntimeError(f"Gemini embedding failed: {last_err}") from last_err


def embed_text(text: str) -> List[float]:
    """Return the embedding vector for the provided text."""

    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("Cannot embed empty text")

    if is_gemini_provider():
        return _embed_with_gemini(cleaned)
    return _embed_with_openai(cleaned)


def visit_summary_doc_id(session_id: str, visit_no: int) -> str:
    return f"{session_id}:v{int(visit_no)}"


def message_doc_id(session_id: str, visit_no: int, turn_no: int) -> str:
    return f"{session_id}:m:{int(visit_no)}:{int(turn_no)}"


def store_visit_summary_embedding(
    session_id: str,
    visit_no: int,
    text: str,
    *,
    db: Optional[SupabaseClient] = None,
) -> Dict[str, Any]:
    """Embed and store a visit summary (one per session/visit)."""

    vector = embed_text(text)
    database = _resolve_db(db)
    filters = {"session_id": session_id, "visit_number": int(visit_no)}
    updated = database.table("visit_summaries").update(
        {"embedding": vector},
        filters=filters,
        returning=True,
    )
    if not updated:
        database.table("visit_summaries").insert(
            {
                "session_id": session_id,
                "visit_number": int(visit_no),
                "summary": text,
                "embedding": vector,
            },
            returning=False,
        )
    return {"embedding": vector}


def store_message_embedding(
    session_id: str,
    visit_no: int,
    turn_no: int,
    role: str,
    text: str,
    *,
    db: Optional[SupabaseClient] = None,
) -> Dict[str, Any]:
    """Supabase schema has no message embedding column; no-op for now."""

    return {}


def store_case_chunk_embedding(
    case_id: str,
    chunk_id: str,
    text: str,
    *,
    visit_no: Optional[int] = None,
    kind: Optional[str] = None,
    db: Optional[SupabaseClient] = None,
) -> Dict[str, Any]:
    """Supabase schema has no case chunk embedding table; no-op for now."""

    return {}


def retrieve_context(
    session_id: str,
    query: str,
    *,
    db: Optional[SupabaseClient] = None,
    visit_no: Optional[int] = None,
    top_summaries: int = 3,
    top_messages: int = 5,
    top_case_chunks: int = 5,
    include_messages: bool = True,
    include_case_chunks: bool = True,
) -> Dict[str, Any]:
    """
    Supabase-backed retrieval: return recent visit summaries only.
    """

    database = _resolve_db(db)
    summaries: List[Dict[str, Any]] = []
    if top_summaries > 0:
        filters: Dict[str, Any] = {"session_id": session_id}
        if visit_no is not None:
            filters["visit_number"] = ("lte", int(visit_no))
        rows = database.table("visit_summaries").select(
            filters=filters,
            order=("visit_number", "desc"),
            limit=top_summaries,
        )
        for row in rows:
            summaries.append(
                {
                    "doc_id": visit_summary_doc_id(session_id, row.get("visit_number") or 0),
                    "text": row.get("summary", ""),
                    "visit_no": row.get("visit_number"),
                }
            )

    return {"summaries": summaries, "messages": [], "case_chunks": []}


__all__ = [
    "embed_text",
    "store_visit_summary_embedding",
    "store_message_embedding",
    "store_case_chunk_embedding",
    "retrieve_context",
]
