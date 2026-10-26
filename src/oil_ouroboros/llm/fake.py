"""
Детерминированная «модель» для тестов и офлайн-разработки (LLM_PROVIDER=fake): правила по ключевым словам вместо LLM.

Нужна, чтобы субагенты, проверки и eval-харнесс тестировались без сети и без денег. Качество ответов — не цель.
"""

from __future__ import annotations

import json
import re

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

OUT_OF_SCOPE = ("рецепт", "погод", "футбол", "анекдот", "стих", "recipe", "weather", "борщ", "фильм")
FRESH = ("сегодня", "сейчас", "новост", "последн", "текущ", "на этой неделе", "today", "news", "latest")


class ScriptedChat(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        system = " ".join(str(m.content) for m in messages if m.type == "system")
        last_user = next((str(m.content) for m in reversed(messages) if m.type == "human"), "")
        asked = last_user.split("Фрагменты:")[
            0
        ].lower()  # только сам вопрос: слово «latest» во фрагменте — не свежесть
        if "[[research_plan]]" in system:
            q = asked.replace("вопрос:", "").strip()
            base = re.search(r"Агентства в базе: ([^\n]+)", system)  # агентства базы — из промпта планировщика
            body = {
                "in_scope": not any(w in q for w in OUT_OF_SCOPE),
                "needs_freshness": any(w in q for w in FRESH),
                "freshness_reason": "fake",
                "queries": [q[:200], "oil market " + q[:120]],
                "web_queries": [q[:120]],
                "orgs": [
                    o for o in re.findall(r"\b[A-Z][A-Z0-9]+\b", base.group(1) if base else "") if o.lower() in q
                ],
                "editions": [],
            }
            return self._wrap(AIMessage(content=json.dumps(body, ensure_ascii=False)))
        if "[[select_grade]]" in system:
            n = len(re.findall(r"^#\d+ ", last_user, flags=re.M))
            fresh = any(w in asked for w in FRESH)
            body = {
                "selected": list(range(1, min(n, 6) + 1)),
                "verdict": "partial" if fresh or not n else "sufficient",
                "missing": [],
                "freshness_gap": False,
                "reason": "fake",
            }
            return self._wrap(AIMessage(content=json.dumps(body, ensure_ascii=False)))
        return self._wrap(AIMessage(content="{}"))

    @staticmethod
    def _wrap(msg: AIMessage) -> ChatResult:
        msg.usage_metadata = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
        return ChatResult(generations=[ChatGeneration(message=msg)])
