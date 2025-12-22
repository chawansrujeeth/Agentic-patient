from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import ValidationError

# If you use the official SDK:
from openai import OpenAI  # openai-python client :contentReference[oaicite:3]{index=3}

from ai_provider import is_gemini_provider
from llm_models import PatientResponse  # or import directly if in same file


@dataclass(frozen=True)
class LLMResult:
    parsed: PatientResponse
    usage: Optional[Dict[str, Any]] = None
    raw_text: Optional[str] = None


@dataclass(frozen=True)
class SummaryResult:
    summary_text: str
    usage: Optional[Dict[str, Any]] = None
    raw_text: Optional[str] = None


def build_prompt(
    *,
    visit_no: int,
    level: int,
    doctor_message: str,
    allowed_facts: List[Dict[str, Any]],
    already_disclosed_fact_ids: Optional[List[str]] = None,
    last_messages: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Render the strict instruction block we send to the LLM."""

    allowed_facts_json = json.dumps(allowed_facts, ensure_ascii=False, indent=2)
    disclosed_ids = already_disclosed_fact_ids or []
    disclosed_json = json.dumps(disclosed_ids, ensure_ascii=False)

    history_block = ""
    if last_messages:
        trimmed = [{"role": m.get("role", ""), "content": m.get("content", "")} for m in last_messages]
        history_json = json.dumps(trimmed, ensure_ascii=False, indent=2)
        history_block = f"Recent conversation (last {len(trimmed)} messages):\n{history_json}\n\n"

    prompt = (
        "You are simulating a patient in a medical training game.\n\n"
        "Hard rules:\n"
        "- You MUST NOT reveal any information that is not explicitly present in the AllowedFacts list below.\n"
        "- You MUST ONLY disclose new facts by returning their IDs in new_disclosed_fact_ids.\n"
        "- Every ID you output in new_disclosed_fact_ids MUST be from AllowedFacts.\n"
        "- If the doctor requests an exam/test and no matching AllowedFacts exist, say it is not available at this stage.\n"
        "- Output MUST be valid JSON matching the PatientResponse schema. No extra keys.\n"
        "Context:\n"
        f"visit_no: {visit_no}\n"
        f"doctor_level: {level}\n\n"
        "Doctor message:\n"
        f"{doctor_message}\n\n"
        f"{history_block}"
        "AllowedFacts (ONLY source of truth):\n"
        f"{allowed_facts_json}\n\n"
        "Already disclosed fact IDs (avoid repeating as \"new\"):\n"
        f"{disclosed_json}\n\n"
        "Return JSON now."
    )
    return prompt


def _env(name: str, default: Optional[str] = None) -> str:
    v = os.getenv(name, default)
    if v is None or v == "":
        raise RuntimeError(f"Missing required env var: {name}")
    return v


def _get_openai_client() -> OpenAI:
    # Centralize config here
    api_key = _env("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")
    kwargs: Dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    # NOTE: client init parameters can vary by SDK version; keep this minimal.
    return OpenAI(**kwargs)


@dataclass(frozen=True)
class _GeminiClient:
    mode: Literal["genai", "legacy"]
    client: Any
    types: Any = None


_GEMINI_SDK: Optional[_GeminiClient] = None
_GEMINI_SDKS: Dict[str, _GeminiClient] = {}
_GEMINI_MODELS: Dict[Tuple[str, str], Any] = {}


class QuotaExhaustedError(RuntimeError):
    pass


def _gemini_api_key() -> str:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Missing required env var: set GEMINI_API_KEY or GOOGLE_API_KEY")
    return api_key


def _split_env_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    parts = [item.strip() for item in re.split(r"[,\n;]+", raw) if item.strip()]
    return parts


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
    return [_gemini_api_key()]


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
    """Lazy import/config for google-genai (fallback to legacy google-generativeai)."""

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
        timeout_val = (
            _timeout_ms(os.getenv("GEMINI_TIMEOUT_S"))
            or _timeout_ms(os.getenv("OPENAI_TIMEOUT_S"))
            or _timeout_ms("20")
        )
        http_kwargs: Dict[str, Any] = {"api_version": api_version}
        if timeout_val is not None:
            http_kwargs["timeout"] = timeout_val
        http_options = genai_types.HttpOptions(**http_kwargs)
        client = genai_client.Client(api_key=key, http_options=http_options)
        sdk = _GeminiClient(mode="genai", client=client, types=genai_types)
        _GEMINI_SDKS[key] = sdk
        if api_key is None:
            _GEMINI_SDK = sdk
        return sdk

    try:
        import google.generativeai as legacy_genai
    except ImportError as exc:  # pragma: no cover - import error surfaces when running
        raise RuntimeError(
            "Gemini support requires the google-genai package. Install it with: pip install google-genai"
        ) from exc

    legacy_genai.configure(api_key=key)
    sdk = _GeminiClient(mode="legacy", client=legacy_genai)
    _GEMINI_SDKS[key] = sdk
    if api_key is None:
        _GEMINI_SDK = sdk
    return sdk


def _get_legacy_gemini_model(genai_module: Any, model_name: str, api_key: str) -> Any:
    cache_key = (api_key, model_name)
    if cache_key not in _GEMINI_MODELS:
        genai_module.configure(api_key=api_key)
        _GEMINI_MODELS[cache_key] = genai_module.GenerativeModel(model_name)
    return _GEMINI_MODELS[cache_key]


def _gemini_usage_dict(response: Any) -> Optional[Dict[str, Any]]:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    for attr in ("to_dict", "model_dump", "as_dict"):
        fn = getattr(usage, attr, None)
        if callable(fn):
            try:
                data = fn()
                if isinstance(data, dict):
                    return data
            except Exception:
                continue
    try:
        return dict(usage.__dict__)
    except Exception:
        try:
            return {"usage_raw": str(usage)}
        except Exception:
            return {"usage_raw": "<unserializable>"}


def _extract_gemini_text(response: Any) -> str:
    def _strip_code_fence(value: str) -> str:
        trimmed = value.strip()
        if not trimmed.startswith("```"):
            return value
        body = trimmed[3:]
        newline_idx = body.find("\n")
        if newline_idx != -1:
            body = body[newline_idx + 1 :]
        body = body.rstrip()
        if body.endswith("```"):
            body = body[:-3]
        body = body.strip()
        return body or value

    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return _strip_code_fence(text)

    pieces: List[str] = []
    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or getattr(cand, "parts", None) or []
        for part in parts:
            part_text = getattr(part, "text", None)
            if part_text:
                pieces.append(_strip_code_fence(str(part_text)))
    joined = "\n".join(pieces).strip()
    if joined:
        return joined
    raise RuntimeError("Gemini response did not include any text output")


def _raise_if_blocked(response: Any) -> None:
    feedback = getattr(response, "prompt_feedback", None)
    if not feedback:
        return
    block_reason = getattr(feedback, "block_reason", None)
    if not block_reason:
        return
    reason_str = str(block_reason).upper()
    if "UNSPECIFIED" in reason_str:
        return
    raise RuntimeError(f"Gemini blocked the request: {reason_str}")


def _gemini_generate(
    prompt: str,
    *,
    model_name: str,
    temperature: float,
    timeout_s: float,
    api_key: Optional[str] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    gemini_sdk = _get_gemini_sdk(api_key)
    if gemini_sdk.mode == "genai":
        config = {"temperature": temperature}
        response = gemini_sdk.client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=config,
        )
    else:
        if not api_key:
            api_key = _gemini_api_key()
        model = _get_legacy_gemini_model(gemini_sdk.client, model_name, api_key)
        response = model.generate_content(
            prompt,
            generation_config={"temperature": temperature},
            request_options={"timeout": timeout_s} if timeout_s else None,
        )
    _raise_if_blocked(response)
    raw_text = _extract_gemini_text(response)
    usage = _gemini_usage_dict(response)
    return raw_text, usage


def call_patient_agent(prompt: str) -> LLMResult:
    if is_gemini_provider():
        return _call_patient_agent_gemini(prompt)
    return _call_patient_agent_openai(prompt)


def _call_patient_agent_openai(prompt: str) -> LLMResult:
    """
    One controlled LLM call per doctor turn.
    Returns a validated PatientResponse.
    """

    model = os.getenv("OPENAI_MODEL", "llama-3.1-8b-instant")  # choose your default
    timeout_s = float(os.getenv("OPENAI_TIMEOUT_S", "20"))
    max_retries = int(os.getenv("OPENAI_MAX_RETRIES", "2"))

    client = _get_openai_client()

    last_err: Optional[Exception] = None
    use_json_schema = os.getenv("LLM_USE_JSON_SCHEMA", "1").lower() not in ("0", "false", "no")
    if "llama-3.1-8b-instant" in model:
        use_json_schema = False

    base_prompt = prompt
    prompt_to_send = base_prompt

    for attempt in range(max_retries + 1):
        try:
            request_kwargs: Dict[str, Any] = {
                "model": model,
                "input": prompt_to_send,
                "temperature": 0,
                "timeout": timeout_s,
            }
            if use_json_schema:
                request_kwargs["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": "PatientResponse",
                        "schema": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "patient_utterance": {"type": "string"},
                                "new_disclosed_fact_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "requested_clarifications": {
                                    "type": ["array", "null"],
                                    "items": {"type": "string"},
                                },
                                "visit_end_recommendation": {"type": "boolean"},
                                "safety_flags": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": [
                                "patient_utterance",
                                "new_disclosed_fact_ids",
                                "visit_end_recommendation",
                                "safety_flags",
                            ],
                        },
                        "strict": True,
                    }
                }

            resp = client.responses.create(**request_kwargs)

            # Typical extraction pattern: get the text output, parse JSON.
            # SDK response shape can vary; adjust if needed.
            raw_text = resp.output_text
            data = json.loads(raw_text)
            if not isinstance(data, dict):
                raise ValueError("LLM output was not a JSON object")
            data.setdefault("patient_utterance", "")
            data.setdefault("new_disclosed_fact_ids", [])
            data.setdefault("requested_clarifications", None)
            if data.get("requested_clarifications") is None:
                data["requested_clarifications"] = None
            visit_end_val = data.get("visit_end_recommendation", False)
            if isinstance(visit_end_val, str):
                lowered = visit_end_val.strip().lower()
                if lowered in ("true", "yes", "end", "finish", "close", "stop"):
                    visit_end_val = True
                elif lowered in ("false", "no", "continue", "not yet", "not now", "keep going"):
                    visit_end_val = False
                elif lowered.startswith("not available"):
                    visit_end_val = False
                else:
                    visit_end_val = False
            elif visit_end_val is None:
                visit_end_val = False
            data["visit_end_recommendation"] = bool(visit_end_val)
            data.setdefault("safety_flags", [])
            parsed = PatientResponse.model_validate(data)

            usage: Optional[Dict[str, Any]] = None
            resp_usage = getattr(resp, "usage", None)
            if resp_usage is not None:
                if isinstance(resp_usage, dict):
                    usage = resp_usage
                else:
                    dump_fn = getattr(resp_usage, "model_dump", None)
                    if callable(dump_fn):
                        try:
                            usage = dump_fn()
                        except Exception:
                            usage = None
                if usage is None:
                    try:
                        usage = {"usage_raw": str(resp_usage)}
                    except Exception:
                        usage = {"usage_raw": "<unserializable>"}

            return LLMResult(parsed=parsed, usage=usage, raw_text=raw_text)

        except (json.JSONDecodeError, ValidationError) as e:
            last_err = e
            if attempt >= max_retries:
                break
            warning = (
                "SYSTEM: Your last response was not valid PatientResponse JSON. "
                "You must output ONLY JSON with keys "
                "patient_utterance, new_disclosed_fact_ids, requested_clarifications, "
                "visit_end_recommendation, safety_flags. Do not include any extra text.\n"
                "Regenerate now using the same instructions."
            )
            prompt_to_send = f"{warning}\n\n{base_prompt}"
            time.sleep(0.5 * (2**attempt))
            continue

        except Exception as e:
            last_err = e
            if attempt >= max_retries:
                break
            time.sleep(0.5 * (2**attempt))

    raise RuntimeError(f"LLM call failed after retries: {last_err}") from last_err


def _is_quota_error(err: Exception) -> bool:
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    if isinstance(code, int) and code == 429:
        return True
    name = err.__class__.__name__.lower()
    if "resourceexhausted" in name or "toomanyrequests" in name or "ratelimit" in name:
        return True
    text = str(err).lower()
    return (
        "resource_exhausted" in text
        or "quota" in text
        or "rate limit" in text
        or "429" in text
    )


def _gemini_model_list() -> List[str]:
    raw = os.getenv("GEMINI_MODELS")
    models = _split_env_list(raw)
    if models:
        return models
    models = []
    base_model = os.getenv("GEMINI_MODEL") or os.getenv("OPENAI_MODEL") or "gemini-2.5-flash"
    if base_model and base_model.strip():
        models.append(base_model.strip())
    models.extend(_numbered_env_values("GEMINI_MODEL"))
    models = _dedupe_list(models)
    return models or ["gemini-2.5-flash"]


def _gemini_summary_model_list() -> List[str]:
    raw = os.getenv("GEMINI_SUMMARY_MODELS") or os.getenv("GEMINI_MODELS")
    models = _split_env_list(raw)
    if models:
        return models
    models = []
    base_model = (
        os.getenv("GEMINI_SUMMARY_MODEL")
        or os.getenv("GEMINI_MODEL")
        or os.getenv("OPENAI_SUMMARY_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "gemini-2.5-flash"
    )
    if base_model and base_model.strip():
        models.append(base_model.strip())
    models.extend(_numbered_env_values("GEMINI_SUMMARY_MODEL"))
    models = _dedupe_list(models)
    return models or _gemini_model_list()


def _call_patient_agent_gemini(prompt: str) -> LLMResult:
    models = _gemini_model_list()
    timeout_s = float(os.getenv("GEMINI_TIMEOUT_S") or os.getenv("OPENAI_TIMEOUT_S", "20"))
    max_retries = int(os.getenv("GEMINI_MAX_RETRIES") or os.getenv("OPENAI_MAX_RETRIES", "2"))

    base_prompt = prompt
    last_err: Optional[Exception] = None
    quota_only_failures = True

    for api_key in _gemini_api_keys():
        for model in models:
            prompt_to_send = base_prompt
            quota_error = False
            for attempt in range(max_retries + 1):
                try:
                    raw_text, usage = _gemini_generate(
                        prompt_to_send,
                        model_name=model,
                        temperature=0,
                        timeout_s=timeout_s,
                        api_key=api_key,
                    )
                    data = json.loads(raw_text)
                    if not isinstance(data, dict):
                        raise ValueError("LLM output was not a JSON object")
                    data.setdefault("patient_utterance", "")
                    data.setdefault("new_disclosed_fact_ids", [])
                    data.setdefault("requested_clarifications", None)
                    if data.get("requested_clarifications") is None:
                        data["requested_clarifications"] = None
                    visit_end_val = data.get("visit_end_recommendation", False)
                    if isinstance(visit_end_val, str):
                        lowered = visit_end_val.strip().lower()
                        if lowered in ("true", "yes", "end", "finish", "close", "stop"):
                            visit_end_val = True
                        elif lowered in ("false", "no", "continue", "not yet", "not now", "keep going"):
                            visit_end_val = False
                        elif lowered.startswith("not available"):
                            visit_end_val = False
                        else:
                            visit_end_val = False
                    elif visit_end_val is None:
                        visit_end_val = False
                    data["visit_end_recommendation"] = bool(visit_end_val)
                    data.setdefault("safety_flags", [])
                    parsed = PatientResponse.model_validate(data)
                    return LLMResult(parsed=parsed, usage=usage, raw_text=raw_text)

                except (json.JSONDecodeError, ValidationError) as e:
                    last_err = e
                    if attempt >= max_retries:
                        break
                    warning = (
                        "SYSTEM: Your last response was not valid PatientResponse JSON. "
                        "You must output ONLY JSON with keys "
                        "patient_utterance, new_disclosed_fact_ids, requested_clarifications, "
                        "visit_end_recommendation, safety_flags. Do not include any extra text.\n"
                        "Regenerate now using the same instructions."
                    )
                    prompt_to_send = f"{warning}\n\n{base_prompt}"
                    time.sleep(0.5 * (2**attempt))
                    continue

                except Exception as e:
                    last_err = e
                    if _is_quota_error(e):
                        quota_error = True
                        break
                    if attempt >= max_retries:
                        break
                    time.sleep(0.5 * (2**attempt))
            if quota_error:
                continue
            quota_only_failures = False

    if quota_only_failures and last_err is not None:
        raise QuotaExhaustedError(
            "Gemini quota reached for all configured keys/models. Please come back tomorrow."
        ) from last_err
    raise RuntimeError(f"LLM call failed after retries: {last_err}") from last_err


def call_summary_agent(prompt: str) -> SummaryResult:
    if is_gemini_provider():
        return _call_summary_agent_gemini(prompt)
    return _call_summary_agent_openai(prompt)


def _call_summary_agent_openai(prompt: str) -> SummaryResult:
    """
    Single-call summary generator expecting {"summary_text": "..."} JSON output.
    """

    model = os.getenv("OPENAI_SUMMARY_MODEL") or os.getenv("OPENAI_MODEL", "llama-3.1-8b-instant")
    timeout_s = float(os.getenv("OPENAI_SUMMARY_TIMEOUT_S") or os.getenv("OPENAI_TIMEOUT_S", "20"))
    max_retries = int(os.getenv("OPENAI_SUMMARY_MAX_RETRIES") or os.getenv("OPENAI_MAX_RETRIES", "2"))

    client = _get_openai_client()
    base_prompt = prompt
    prompt_to_send = base_prompt
    last_err: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            resp = client.responses.create(
                model=model,
                input=prompt_to_send,
                temperature=0,
                timeout=timeout_s,
            )
            raw_text = resp.output_text
            data = json.loads(raw_text)
            if not isinstance(data, dict):
                raise ValueError("Summary output was not a JSON object")
            summary_text = str(data.get("summary_text", "") or "").strip()
            if not summary_text:
                raise ValueError("Missing summary_text in response")

            usage: Optional[Dict[str, Any]] = None
            resp_usage = getattr(resp, "usage", None)
            if resp_usage is not None:
                if isinstance(resp_usage, dict):
                    usage = resp_usage
                else:
                    dump_fn = getattr(resp_usage, "model_dump", None)
                    if callable(dump_fn):
                        try:
                            usage = dump_fn()
                        except Exception:
                            usage = None
                if usage is None:
                    try:
                        usage = {"usage_raw": str(resp_usage)}
                    except Exception:
                        usage = {"usage_raw": "<unserializable>"}

            return SummaryResult(summary_text=summary_text, usage=usage, raw_text=raw_text)

        except (json.JSONDecodeError, ValueError) as e:
            last_err = e
            if attempt >= max_retries:
                break
            warning = (
                "SYSTEM: Output MUST be JSON with exactly one key summary_text (string). "
                "Regenerate using the same instructions.\n"
            )
            prompt_to_send = f"{warning}\n\n{base_prompt}"
            time.sleep(0.5 * (2**attempt))
            continue
        except Exception as e:
            last_err = e
            if attempt >= max_retries:
                break
            time.sleep(0.5 * (2**attempt))

    raise RuntimeError(f"Summary LLM call failed after retries: {last_err}") from last_err


def _call_summary_agent_gemini(prompt: str) -> SummaryResult:
    models = _gemini_summary_model_list()
    timeout_s = float(
        os.getenv("GEMINI_SUMMARY_TIMEOUT_S")
        or os.getenv("GEMINI_TIMEOUT_S")
        or os.getenv("OPENAI_SUMMARY_TIMEOUT_S")
        or os.getenv("OPENAI_TIMEOUT_S", "20")
    )
    max_retries = int(
        os.getenv("GEMINI_SUMMARY_MAX_RETRIES")
        or os.getenv("GEMINI_MAX_RETRIES")
        or os.getenv("OPENAI_SUMMARY_MAX_RETRIES")
        or os.getenv("OPENAI_MAX_RETRIES", "2")
    )

    base_prompt = prompt
    last_err: Optional[Exception] = None
    quota_only_failures = True

    for api_key in _gemini_api_keys():
        for model in models:
            prompt_to_send = base_prompt
            quota_error = False
            for attempt in range(max_retries + 1):
                try:
                    raw_text, usage = _gemini_generate(
                        prompt_to_send,
                        model_name=model,
                        temperature=0,
                        timeout_s=timeout_s,
                        api_key=api_key,
                    )
                    data = json.loads(raw_text)
                    if not isinstance(data, dict):
                        raise ValueError("Summary output was not a JSON object")
                    summary_text = str(data.get("summary_text", "") or "").strip()
                    if not summary_text:
                        raise ValueError("Missing summary_text in response")
                    return SummaryResult(summary_text=summary_text, usage=usage, raw_text=raw_text)

                except (json.JSONDecodeError, ValueError) as e:
                    last_err = e
                    if attempt >= max_retries:
                        break
                    warning = (
                        "SYSTEM: Output MUST be JSON with exactly one key summary_text (string). "
                        "Regenerate using the same instructions.\n"
                    )
                    prompt_to_send = f"{warning}\n\n{base_prompt}"
                    time.sleep(0.5 * (2**attempt))
                    continue
                except Exception as e:
                    last_err = e
                    if _is_quota_error(e):
                        quota_error = True
                        break
                    if attempt >= max_retries:
                        break
                    time.sleep(0.5 * (2**attempt))
            if quota_error:
                continue
            quota_only_failures = False

    if quota_only_failures and last_err is not None:
        raise QuotaExhaustedError(
            "Gemini quota reached for all configured keys/models. Please come back tomorrow."
        ) from last_err
    raise RuntimeError(f"Summary LLM call failed after retries: {last_err}") from last_err
