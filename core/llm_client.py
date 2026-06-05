"""
core/llm_client.py
LLM 统一调用层 - 支持 DeepSeek / OpenAI / Claude / Gemini
新增：
  - Gemini 视觉调用（describe_image）
  - DeepSeek thinking 模式默认关闭
"""

import os
import json
import requests
from typing import List, Dict, Optional


class LLMClient:

    PROVIDER_URLS = {
        "deepseek": "https://api.deepseek.com/chat/completions",
        "openai":   "https://api.openai.com/v1/chat/completions",
        "claude":   "https://api.anthropic.com/v1/messages",
        "gemini":   "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
    }

    def __init__(self, provider: str, model: str, api_key: str):
        self.provider = provider.lower()
        self.model    = model

        if api_key.startswith("env:"):
            env_var      = api_key[4:]
            self.api_key = os.environ.get(env_var, "")
            if not self.api_key:
                print(f"[警告] 环境变量 {env_var} 未设置，请检查 .env 文件")
        else:
            self.api_key = api_key

        if self.provider not in self.PROVIDER_URLS:
            raise ValueError(f"不支持的provider: {provider}，可选: deepseek/openai/claude/gemini")

    def chat(self, messages: List[Dict], system_prompt: str = "",
             max_tokens: int = 500, temperature: float = 0.8) -> str:
        try:
            if self.provider in ("deepseek", "openai"):
                return self._call_openai_compatible(messages, system_prompt, max_tokens, temperature)
            elif self.provider == "claude":
                return self._call_claude(messages, system_prompt, max_tokens, temperature)
            elif self.provider == "gemini":
                return self._call_gemini(messages, system_prompt, max_tokens, temperature)
        except Exception as e:
            print(f"[LLM错误] {self.provider} 调用失败: {e}")
            return "（网络好像有点问题，稍后再试试吧）"

    def chat_json(self, messages: List[Dict], system_prompt: str = "",
                  max_tokens: int = 300, temperature: float = 0.3) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json"
        }
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        payload = {
            "model":           self.model,
            "messages":        full_messages,
            "max_tokens":      max_tokens,
            "temperature":     temperature,
            "response_format": {"type": "json_object"},
            "thinking":        {"type": "disabled"},
        }

        resp = requests.post(
            self.PROVIDER_URLS[self.provider],
            headers=headers, json=payload, timeout=30
        )
        resp.raise_for_status()
        data    = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        if not content:
            raise ValueError("API返回空内容")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            if content.startswith("```"):
                content = content.strip("`").strip()
                if content.startswith("json"):
                    content = content[4:].strip()
            return json.loads(content)

    # ── OpenAI 兼容格式（DeepSeek / OpenAI）────────────────────────────
    def _call_openai_compatible(self, messages, system_prompt, max_tokens, temperature):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json"
        }
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        payload = {
            "model":       self.model,
            "messages":    full_messages,
            "max_tokens":  max_tokens,
            "temperature": temperature,
            "thinking":    {"type": "disabled"},  # 关闭 thinking 模式
        }

        resp = requests.post(
            self.PROVIDER_URLS[self.provider],
            headers=headers, json=payload, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    # ── Claude 格式 ──────────────────────────────────────────────────────
    def _call_claude(self, messages, system_prompt, max_tokens, temperature):
        headers = {
            "x-api-key":         self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type":      "application/json"
        }
        payload = {
            "model":       self.model,
            "max_tokens":  max_tokens,
            "temperature": temperature,
            "messages":    messages,
        }
        if system_prompt:
            payload["system"] = system_prompt

        resp = requests.post(
            self.PROVIDER_URLS["claude"],
            headers=headers, json=payload, timeout=30
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"].strip()

    # ── Gemini 格式（文字对话）───────────────────────────────────────────
    def _call_gemini(self, messages, system_prompt, max_tokens, temperature):
        url = self.PROVIDER_URLS["gemini"].format(model=self.model)
        url += f"?key={self.api_key}"

        contents = []
        if system_prompt:
            contents.append({"role": "user",  "parts": [{"text": f"[系统设定]\n{system_prompt}"}]})
            contents.append({"role": "model", "parts": [{"text": "好的，我明白了。"}]})

        for msg in messages:
            role = "model" if msg["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})

        payload = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature":     temperature,
            }
        }

        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

    # ── Gemini 视觉：描述图片 ────────────────────────────────────────────
    def describe_image(self, image_base64: str, caption: str = "") -> str:
        """
        用 Gemini 1.5 Flash 描述图片内容
        返回一段中文描述，供后续传给 DeepSeek
        """
        google_api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not google_api_key:
            print("[图片] GOOGLE_API_KEY 未设置")
            return "（图片无法读取）"

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={google_api_key}"

        caption_hint = f"用户发送这张图片时说：「{caption}」\n\n" if caption and caption != "（发了一张图）" else ""

        prompt_text = f"""{caption_hint}请用中文简洁描述这张图片的内容（3-5句话）。
描述要客观具体，包括：主要内容、颜色、氛围、细节。
不要评价好坏，只描述看到的内容。"""

        payload = {
            "contents": [{
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data":      image_base64
                        }
                    },
                    {
                        "text": prompt_text
                    }
                ]
            }],
            "generationConfig": {
                "maxOutputTokens": 200,
                "temperature":     0.2,
            }
        }

        try:
            resp = requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            description = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            print(f"[图片描述] {description[:60]}...")
            return description
        except Exception as e:
            print(f"[图片描述✗] {e}")
            return "（图片内容无法解析）"
