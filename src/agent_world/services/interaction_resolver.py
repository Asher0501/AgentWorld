"""
Interaction Resolver —— 将每个 NPC 的独立 prompt 发送给 LLM，返回该 NPC 的交互指令。

后端支持：
- MiniMax（Anthropic 兼容 API）
- OpenAI（Chat Completions API）

自动检测 API key：环境变量 > openclaw.json > agent models.json
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .graph_engine import GraphEngine

logger = logging.getLogger("interaction_resolver")


def _find_api_credentials() -> tuple[str, str, str]:
    """
    查找 LLM API 凭证。
    Returns: (base_url, api_key, provider)
    provider: "minimax" | "openai"
    """
    # 环境变量优先
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    base_url = os.environ.get("MINIMAX_BASE_URL", "").strip()
    if api_key and base_url and "minimax" in base_url.lower():
        return base_url, api_key, "minimax"

    # OpenAI 环境变量
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if api_key:
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        return base_url, api_key, "openai"

    # 从配置文件中查找
    config_paths = [
        os.path.expanduser("~/.openclaw/agents/coder/agent/models.json"),
        os.path.expanduser("~/.openclaw/agents/life/agent/models.json"),
        os.path.expanduser("~/.openclaw/openclaw.json"),
    ]

    for path in config_paths:
        try:
            with open(path) as f:
                raw = f.read()
            # 找到 providers 段
            idx = raw.find('"providers"')
            if idx < 0:
                continue
            brace_idx = raw.find('{', idx)
            if brace_idx < 0:
                continue
            partial = raw[brace_idx:]
            depth = 0
            end = 0
            for i, c in enumerate(partial):
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end == 0:
                continue
            providers = json.loads(partial[:end])

            # MiniMax
            mm = providers.get("minimax", {})
            k = mm.get("apiKey", "") or mm.get("api_key", "")
            u = mm.get("baseUrl", "") or mm.get("base_url", "")
            if k and u:
                return u, k, "minimax"

            # OpenAI
            oa = providers.get("openai", {})
            k = oa.get("apiKey", "") or oa.get("api_key", "")
            u = oa.get("baseUrl", "") or oa.get("base_url", "https://api.openai.com/v1")
            if k:
                return u, k, "openai"
        except Exception:
            continue

    return "", "", ""


class InteractionResolver:
    """
    将 NPC 独立 prompt 发送给 LLM，解析返回的结构化 JSON 交互指令。

    每个 NPC 独立调用（或合并为一次调用但保持 NPC 独立性）。
    """

    def __init__(self, model: str | None = None, temperature: float = 0.7):
        base_url, api_key, provider = _find_api_credentials()
        if not api_key:
            raise ValueError("未配置 LLM API key（检查 MINIMAX_API_KEY 或 OPENAI_API_KEY）")

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.provider = provider

        if model is None:
            if provider == "minimax":
                model = "MiniMax-M2.7"
            else:
                model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.model = model
        self.temperature = temperature

    # ─── Prompt 合并（供 engine.run_stage_plan_combined 使用）───

    def _build_combined_prompt(self, npc_prompts: list[tuple[str, str]]) -> str:
        """将多个 NPC 的独立 prompt 合并为一个 LLM 调用"""
        parts = [
            "你是一个世界模拟引擎的交互推理模块。",
            "以下是多个 NPC 的独立决策请求，请为每个 NPC 输出一条 JSON 结果。",
            "每个 NPC 互相独立，不要跨 NPC 推理。",
            "",
            f"共 {len(npc_prompts)} 个 NPC。",
            "",
        ]

        for i, (eid, prompt) in enumerate(npc_prompts):
            parts.append(f"==== NPC {i+1}: {eid} ====")
            parts.append(prompt)
            parts.append("")

        parts.append("==== 输出格式 ====")
        parts.append("""请返回一个 JSON 对象，key 为 NPC 的 entity_id，value 为该 NPC 的自然语言决策描述。

{
  "npc_16a384f6": "我叫老张，我是farmer。我目前在tavern，持有小麦x21、金币x5，但体力只有7/100。我决定前往market卖掉5单位小麦换金币，体力会消耗一些，然后回来休息。",
  "npc_03b2e97d": "..."
}

不要多余文字，不要 markdown 代码块。每个 NPC 必须有一条指令，用第一人称自然语言描述。""")

        return "\n".join(parts)

    def _parse_combined_response(self, text: str) -> dict | list:
        """解析合并响应，尝试提取 JSON"""
        text = text.strip()

        # 策略1：提取 ```json ... ```
        if '```' in text:
            blocks = text.split('```')
            for i, block in enumerate(blocks):
                block = block.strip()
                if block.startswith('json'):
                    block = block[4:].strip()
                if block.startswith('{') or block.startswith('['):
                    text = block
                    break

        # 策略2：提取 JSON 对象
        stack = []
        start = -1
        for i, ch in enumerate(text):
            if ch == '{':
                if not stack:
                    start = i
                stack.append(ch)
            elif ch == '}':
                if stack and stack[-1] == '{':
                    stack.pop()
                    if not stack and start >= 0:
                        text = text[start:i+1]
                        break
        # 尝试一次 JSON 解析
        try:
            result = json.loads(text)
            return result
        except json.JSONDecodeError:
            pass

        # 策略3：提取 JSON 数组
        start = -1
        stack = []
        for i, ch in enumerate(text):
            if ch == '[':
                if not stack:
                    start = i
                stack.append(ch)
            elif ch == ']':
                if stack and stack[-1] == '[':
                    stack.pop()
                    if not stack and start >= 0:
                        text = text[start:i+1]
                        try:
                            return json.loads(text)
                        except json.JSONDecodeError:
                            pass
                        break
        return {}

    # ─── API 调用 ───

    def call_llm(self, prompt: str) -> str:
        """公开的 LLM 调用入口，供外部层使用。"""
        return self._call_llm(prompt)

    def _call_llm(self, prompt: str) -> str:
        if self.provider == "minimax":
            return self._call_minimax(prompt)
        return self._call_openai(prompt)

    def _call_minimax(self, prompt: str) -> str:
        import httpx

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                with httpx.Client(
                    base_url=self.base_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "anthropic-version": "2023-06-01",
                        "anthropic-dangerous-direct-browser-access": "true",
                        "Content-Type": "application/json",
                    },
                    timeout=300.0,
                ) as client:
                    response = client.post(
                        "/v1/messages",
                        json={
                            "model": self.model,
                            "max_tokens": 8000,
                            "temperature": self.temperature,
                            "system": "你只输出 JSON 格式数据，不要推理过程，不要多余文字。",
                            "messages": [
                                {"role": "user", "content": prompt},
                            ],
                        },
                    )
                if response.status_code == 200:
                    data = response.json()
                    content_blocks = data.get("content", [])
                    text_result = ""
                    thinking_result = ""
                    for block in content_blocks:
                        bt = block.get("type", "")
                        if bt == "text":
                            t = block.get("text", "")
                            if t.strip():
                                text_result = t
                        elif bt == "thinking":
                            t = block.get("thinking", "") or block.get("text", "")
                            if t.strip():
                                thinking_result = t
                    result = text_result or thinking_result
                    return result.strip()
                elif response.status_code == 429 and attempt < max_retries:
                    continue
                else:
                    logger.error(f"MiniMax API error {response.status_code}: {response.text[:200]}")
                    return ""
            except Exception as e:
                logger.error(f"MiniMax call error (attempt {attempt+1}): {e}")
                if attempt < max_retries:
                    continue
                return ""
        return ""

    def _call_openai(self, prompt: str) -> str:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是世界模拟引擎的交互推理模块。根据每个 NPC 的独立上下文输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                max_tokens=4000,
            )
            text = response.choices[0].message.content or ""
            return text
        except Exception as e:
            logger.error(f"OpenAI call error: {e}")
            return ""
