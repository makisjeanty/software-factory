"""Camada de LLM plugavel. Zero dependencias (urllib).

Interface unica: llm.complete(role, system, user) -> str
`role` identifica o agente (analyst, architect, coder, tester, fixer); so o
provedor mock o usa para escolher a resposta. Provedores reais ignoram.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


def _post(url: str, payload: dict, headers: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise LLMError(f"HTTP {e.code} from {url}: {e.read()[:500].decode(errors='replace')}") from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise LLMError(f"request to {url} failed: {e}") from e


class Ollama:
    def __init__(self, model: str, host: str):
        self.model, self.host = model, host.rstrip("/")
        self.name = f"ollama:{model}"
        self.is_mock = False

    def complete(self, role: str, system: str, user: str) -> str:
        d = _post(
            f"{self.host}/api/chat",
            {
                "model": self.model,
                "stream": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            {},
        )
        try:
            return d["message"]["content"]
        except (KeyError, TypeError) as e:
            raise LLMError(f"unexpected ollama response: {str(d)[:300]}") from e


class OpenAICompat:
    """OpenAI e qualquer endpoint compativel (OpenRouter, LM Studio, vLLM...)."""

    def __init__(self, model: str, key: str, base: str):
        self.model, self.key, self.base = model, key, base.rstrip("/")
        self.name = f"openai-compat:{model}"
        self.is_mock = False

    def complete(self, role: str, system: str, user: str) -> str:
        d = _post(
            f"{self.base}/chat/completions",
            {
                "model": self.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            {"Authorization": f"Bearer {self.key}"},
        )
        try:
            return d["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected openai response: {str(d)[:300]}") from e


class Anthropic:
    def __init__(self, model: str, key: str):
        self.model, self.key = model, key
        self.name = f"anthropic:{model}"
        self.is_mock = False

    def complete(self, role: str, system: str, user: str) -> str:
        d = _post(
            "https://api.anthropic.com/v1/messages",
            {
                "model": self.model,
                "max_tokens": 16000,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
        )
        try:
            return "".join(b["text"] for b in d["content"] if b.get("type") == "text")
        except (KeyError, TypeError) as e:
            raise LLMError(f"unexpected anthropic response: {str(d)[:300]}") from e


class Gemini:
    def __init__(self, model: str, key: str):
        self.model, self.key = model, key
        self.name = f"gemini:{model}"
        self.is_mock = False

    def complete(self, role: str, system: str, user: str) -> str:
        d = _post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
            },
            {"x-goog-api-key": self.key},
        )
        try:
            return "".join(p["text"] for p in d["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected gemini response: {str(d)[:300]}") from e


class Mock:
    """NAO e um modelo. Respostas fixas para testar o pipeline sem LLM.

    Todo relatorio de entrega gerado com este provedor declara isso.
    """

    name = "mock (NENHUM modelo real foi consultado)"
    is_mock = True

    def complete(self, role: str, system: str, user: str) -> str:
        if role == "analyst":
            idea = user.split("IDEA:\n", 1)[-1].split("\n\n", 1)[0].strip()
            return json.dumps(
                {
                    "summary": idea,
                    "requirements": [{"id": "R1", "text": f"Expor uma funcao que descreve: {idea}", "priority": "must"}],
                    "acceptance_criteria": ["describe() retorna texto contendo a ideia original"],
                    "out_of_scope": ["Tudo alem de um esqueleto minimo (mock)"],
                    "open_questions": [],
                }
            )
        if role == "architect":
            return json.dumps(
                {
                    "stack": {"language": "python", "framework": "stdlib", "test_framework": "unittest"},
                    "files": [
                        {"path": "app.py", "purpose": "Modulo principal com describe()"},
                        {"path": "README.md", "purpose": "Como executar e testar"},
                        {"path": "tests/test_app.py", "purpose": "Testes unittest do modulo principal"},
                    ],
                    "commands": {"test": "python -m unittest discover -s tests -v"},
                    "risks": ["Arquitetura gerada pelo mock: apenas esqueleto"],
                }
            )
        if role in ("coder", "tester"):
            m = re.search(r"^FILE: (.+)$", user, re.M)
            path = m.group(1) if m else ""
            idea = re.search(r"^IDEA: (.+)$", user, re.M)
            text = idea.group(1) if idea else "ideia"
            if path == "app.py":
                return f'IDEA = {text!r}\n\n\ndef describe() -> str:\n    return f"App: {{IDEA}}"\n'
            if path == "tests/test_app.py":
                return (
                    "import unittest\n\nimport app\n\n\nclass TestApp(unittest.TestCase):\n"
                    "    def test_describe_contains_idea(self):\n"
                    "        self.assertIn(app.IDEA, app.describe())\n\n\n"
                    'if __name__ == "__main__":\n    unittest.main()\n'
                )
            return "# App (mock)\n\nExecutar testes: `python -m unittest discover -s tests -v`\n"
        if role == "fixer":
            raise LLMError("mock nao sabe corrigir codigo")
        raise LLMError(f"mock: role desconhecido {role!r}")


def get_llm(env: dict | None = None):
    env = os.environ if env is None else env
    kind = env.get("FACTORY_LLM", "mock").lower()
    model = env.get("FACTORY_MODEL", "")
    if kind == "mock":
        return Mock()
    if not model:
        raise LLMError(f"FACTORY_MODEL e obrigatorio para FACTORY_LLM={kind}")
    if kind == "ollama":
        return Ollama(model, env.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
    keys = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}
    if kind not in keys:
        raise LLMError(f"FACTORY_LLM desconhecido: {kind!r} (use mock|ollama|openai|anthropic|gemini)")
    key = env.get(keys[kind], "")
    if not key:
        raise LLMError(f"{keys[kind]} nao definido")
    if kind == "openai":
        return OpenAICompat(model, key, env.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    return Anthropic(model, key) if kind == "anthropic" else Gemini(model, key)
