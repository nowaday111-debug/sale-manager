"""Kết nối nhiều hãng Vision AI qua HTTP. Đổi hãng = đổi OCR_PROVIDER trong file .env.

DeepSeek, OpenAI, Gemini, Qwen đều dùng chuẩn "OpenAI Chat Completions"; Anthropic dùng Messages API.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field

import httpx

from ..config import settings
from ..errors import BusinessError


@dataclass
class ProviderSpec:
    name: str
    base_url: str
    key_env: str
    default_model: str
    style: str = "openai"            # openai | anthropic
    json_mode: bool = True           # gửi response_format={"type":"json_object"}
    extra_body: dict = field(default_factory=dict)


PROVIDERS: dict[str, ProviderSpec] = {
    # DeepSeek V4.1 Flash – mô hình DeepSeek duy nhất nhận ảnh qua API
    "deepseek": ProviderSpec("deepseek", "https://api.deepseek.com/chat/completions", "DEEPSEEK_API_KEY",
                             "deepseek-flash"),
    "gemini": ProviderSpec("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                           "GEMINI_API_KEY", "gemini-3.1-flash-lite"),
    "qwen": ProviderSpec("qwen", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
                         "DASHSCOPE_API_KEY", "qwen-vl-ocr", json_mode=False),
    "openai": ProviderSpec("openai", "https://api.openai.com/v1/chat/completions", "OPENAI_API_KEY", "gpt-6-luna"),
    "anthropic": ProviderSpec("anthropic", "https://api.anthropic.com/v1/messages", "ANTHROPIC_API_KEY",
                              "claude-haiku-4-5", style="anthropic", json_mode=False),
}


@dataclass
class VisionResult:
    provider: str
    model: str
    text: str
    data: dict
    usage: dict
    seconds: float


def extract_json(text: str) -> dict:
    """Lấy đối tượng JSON từ câu trả lời (bỏ ```json ... ``` nếu có)."""
    if not text:
        raise BusinessError("AI không trả về nội dung", code="ocr_empty")
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE | re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass
    raise BusinessError("Câu trả lời của AI không phải JSON hợp lệ", code="ocr_bad_json")


class VisionClient:
    def __init__(self, provider: str | None = None, model: str | None = None, api_key: str | None = None,
                 timeout: float | None = None, transport: httpx.BaseTransport | None = None):
        name = (provider or settings.ocr_provider).lower()
        if name not in PROVIDERS:
            raise BusinessError(f"Hãng OCR không hỗ trợ: {name}. Chọn một trong {sorted(PROVIDERS)}")
        self.spec = PROVIDERS[name]
        self.model = model or (settings.ocr_model if provider is None else None) or self.spec.default_model
        self.api_key = api_key or os.getenv(self.spec.key_env, "")
        if not self.api_key:
            raise BusinessError(f"Chưa có khóa API: hãy điền {self.spec.key_env} trong file backend/.env",
                                code="ocr_no_key")
        self.timeout = timeout or settings.ocr_timeout
        self._transport = transport

    # ------------------------------------------------------------ request body
    def _openai_body(self, system: str, prompt: str, image_b64: str, json_mode: bool) -> dict:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ]},
            ],
            **self.spec.extra_body,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    def _anthropic_body(self, system: str, prompt: str, image_b64: str) -> dict:
        return {
            "model": self.model, "max_tokens": 4096, "temperature": 0, "system": system,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                {"type": "text", "text": prompt},
            ]}],
        }

    def _headers(self) -> dict:
        if self.spec.style == "anthropic":
            return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    # ------------------------------------------------------------ call
    def read(self, jpeg: bytes, system: str, prompt: str, *, retries: int = 2) -> VisionResult:
        image_b64 = base64.b64encode(jpeg).decode()
        json_mode = self.spec.json_mode
        started = time.monotonic()
        last_error = ""
        with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
            for attempt in range(retries + 1):
                body = (self._anthropic_body(system, prompt, image_b64) if self.spec.style == "anthropic"
                        else self._openai_body(system, prompt, image_b64, json_mode))
                try:
                    resp = client.post(self.spec.base_url, headers=self._headers(), json=body)
                except httpx.HTTPError as exc:
                    last_error = f"Lỗi kết nối {self.spec.name}: {exc}"
                    time.sleep(2 * (attempt + 1))
                    continue
                if resp.status_code == 400 and json_mode and "response_format" in resp.text:
                    json_mode = False          # hãng/mô hình không hỗ trợ JSON mode -> gửi lại không có
                    continue
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_error = f"{self.spec.name} trả lỗi {resp.status_code}: {resp.text[:300]}"
                    time.sleep(3 * (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    raise BusinessError(f"{self.spec.name} trả lỗi {resp.status_code}: {resp.text[:500]}",
                                        code="ocr_provider_error")
                payload = resp.json()
                if self.spec.style == "anthropic":
                    text = "".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text")
                    usage = payload.get("usage", {})
                else:
                    text = payload["choices"][0]["message"].get("content") or ""
                    usage = payload.get("usage", {})
                return VisionResult(self.spec.name, self.model, text, extract_json(text), usage,
                                    round(time.monotonic() - started, 2))
        raise BusinessError(last_error or f"Không gọi được {self.spec.name}", code="ocr_provider_error")


def available_providers() -> list[str]:
    return [name for name, spec in PROVIDERS.items() if os.getenv(spec.key_env)]
