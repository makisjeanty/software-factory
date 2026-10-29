"""Agentes: analise, arquitetura, implementacao (coder/tester) e correcao.

Cada agente e uma funcao pura (llm, entrada) -> saida validada. Saidas
estruturadas sao JSON validado (com 1 retry); arquivos sao texto puro.
"""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

LANG_NOTE = "Write human-readable text in the same language as the idea. Code identifiers in English."


class AgentError(Exception):
    pass


# ---------- utilitarios ----------

def safe_rel(path: str) -> str:
    """Caminho relativo seguro dentro do workspace, ou AgentError."""
    if not isinstance(path, str) or not path.strip():
        raise AgentError(f"unsafe path: {path!r}")
    p = PurePosixPath(path.replace("\\", "/"))
    if not p.parts or p.is_absolute() or ".." in p.parts or p.parts[0] in (".git", ".venv"):
        raise AgentError(f"unsafe path: {path!r}")
    return str(p)


def extract_json(text: str) -> dict:
    i = text.find("{")
    if i < 0:
        raise AgentError("no JSON object in response")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[i:])
    except json.JSONDecodeError as e:
        raise AgentError(f"invalid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise AgentError("JSON root must be an object")
    return obj


def strip_fence(text: str) -> str:
    """Remove cerca ``` somente se a resposta inteira for um bloco cercado."""
    m = re.fullmatch(r"\s*```[\w+.-]*\n(.*)\n```\s*", text, re.S)
    return (m.group(1) if m else text.strip("\n")) + "\n"


def parse_files(text: str) -> dict[str, str]:
    """Formato: '=== FILE: caminho ===' seguido do conteudo."""
    out = {}
    for m in re.finditer(r"^=== FILE: (.+?) ===\n(.*?)(?=^=== FILE: |\Z)", text, re.M | re.S):
        out[safe_rel(m.group(1).strip())] = strip_fence(m.group(2))
    if not out:
        raise AgentError("no '=== FILE: path ===' blocks in response")
    return out


def ask_json(llm, role: str, system: str, user: str, validate) -> dict:
    err = None
    for _ in range(2):
        prompt = user if err is None else f"{user}\n\nYour previous answer was rejected: {err}\nReturn ONLY valid JSON."
        raw = llm.complete(role, system, prompt)
        try:
            obj = extract_json(raw)
            validate(obj)
            return obj
        except AgentError as e:
            err = str(e)
    raise AgentError(f"{role}: invalid output after retry: {err}")


def _is_test(path: str) -> bool:
    low = path.lower()
    return "test" in PurePosixPath(low).name or "/tests/" in f"/{low}" or low.startswith("tests/")


def _context(files: dict[str, str], limit: int = 6000) -> str:
    return "\n".join(f"--- {p} ---\n{c[:limit]}" for p, c in files.items()) or "(none yet)"


# ---------- agente de analise ----------

def _validate_analysis(a: dict) -> None:
    if not isinstance(a.get("summary"), str) or not a["summary"].strip():
        raise AgentError("missing 'summary'")
    reqs = a.get("requirements")
    if not isinstance(reqs, list) or not reqs or not all(isinstance(r, dict) and r.get("id") and r.get("text") for r in reqs):
        raise AgentError("'requirements' must be a non-empty list of {id, text, priority}")
    ac = a.get("acceptance_criteria")
    if not isinstance(ac, list) or not ac:
        raise AgentError("'acceptance_criteria' must be a non-empty list")


def analyze(llm, idea: str, feedback: str = "") -> dict:
    system = (
        "You are a senior business analyst. Turn the idea into a precise, minimal specification. "
        'Return ONLY a JSON object: {"summary": str, "requirements": [{"id": "R1", "text": str, "priority": "must|should|could"}], '
        '"acceptance_criteria": [str], "out_of_scope": [str], "open_questions": [str]}. '
        "Prefer the smallest scope that satisfies the idea; list anything ambiguous in open_questions. " + LANG_NOTE
    )
    user = f"IDEA:\n{idea}\n\n" + (f"HUMAN FEEDBACK TO APPLY:\n{feedback}\n" if feedback else "")
    return ask_json(llm, "analyst", system, user, _validate_analysis)


# ---------- agente de arquitetura ----------

def _validate_architecture(a: dict) -> None:
    st = a.get("stack")
    if not isinstance(st, dict) or not st.get("language"):
        raise AgentError("'stack' must be an object with 'language'")
    files = a.get("files")
    if not isinstance(files, list) or not files:
        raise AgentError("'files' must be a non-empty list of {path, purpose}")
    seen = set()
    for f in files:
        if not isinstance(f, dict) or not f.get("purpose"):
            raise AgentError("every file needs 'path' and 'purpose'")
        f["path"] = safe_rel(f.get("path"))
        if f["path"] in seen:
            raise AgentError(f"duplicate file {f['path']}")
        seen.add(f["path"])
    if not any(_is_test(f["path"]) for f in files):
        raise AgentError("the plan must include at least one test file")
    cmd = (a.get("commands") or {}).get("test")
    if not isinstance(cmd, str) or not cmd.strip():
        raise AgentError("'commands.test' must be a command string run from the project root")


def design(llm, analysis: dict, feedback: str = "") -> dict:
    system = (
        "You are a software architect. Choose the simplest stack that fits and plan every file of a complete, "
        "runnable, tested project. Return ONLY a JSON object: "
        '{"stack": {"language": str, "framework": str, "test_framework": str}, '
        '"files": [{"path": "relative/path", "purpose": str}], '
        '"commands": {"test": "command run from project root, e.g. python -m pytest -q"}, '
        '"risks": [str]}. '
        "Include tests, a README and (if dependencies exist) a requirements file. No absolute paths, no '..'. "
        "Prefer the standard library when it is enough. " + LANG_NOTE
    )
    user = f"SPECIFICATION:\n{json.dumps(analysis, ensure_ascii=False, indent=2)}\n\n" + (
        f"HUMAN FEEDBACK TO APPLY:\n{feedback}\n" if feedback else ""
    )
    return ask_json(llm, "architect", system, user, _validate_architecture)


# ---------- agentes de implementacao ----------

def implement_order(arch: dict) -> list[dict]:
    """Codigo primeiro, testes depois (testes enxergam o codigo)."""
    return sorted(arch["files"], key=lambda f: _is_test(f["path"]))


def write_file(llm, analysis: dict, arch: dict, spec: dict, existing: dict[str, str]) -> str:
    tester = _is_test(spec["path"])
    role = "tester" if tester else "coder"
    system = (
        f"You are a senior {'test engineer' if tester else 'software engineer'}. "
        "Write exactly ONE file. Return ONLY the complete file content: no explanations, no markdown fences "
        "unless the file itself is markdown. The code must run as-is and match the architecture and the "
        "files already written. " + ("Tests must exercise real behaviour and assert on it. " if tester else "") + LANG_NOTE
    )
    user = (
        f"IDEA: {analysis['summary']}\n\n"
        f"SPECIFICATION:\n{json.dumps(analysis, ensure_ascii=False)}\n\n"
        f"ARCHITECTURE:\n{json.dumps(arch, ensure_ascii=False)}\n\n"
        f"FILES ALREADY WRITTEN:\n{_context(existing)}\n\n"
        f"FILE: {spec['path']}\nPURPOSE: {spec['purpose']}\n"
    )
    return strip_fence(llm.complete(role, system, user))


# ---------- agente de correcao (usado pelo ciclo de qualidade) ----------

def fix(llm, analysis: dict, arch: dict, files: dict[str, str], failures: str) -> dict[str, str]:
    system = (
        "You are a senior engineer fixing a failing build. Given the failing quality checks and all project files, "
        "return ONLY the files that must change (or be added), each as a block:\n"
        "=== FILE: relative/path ===\n<complete new file content>\n"
        "No explanations. Fix the root cause; never delete or weaken tests to make them pass. " + LANG_NOTE
    )
    user = (
        f"IDEA: {analysis['summary']}\n\nARCHITECTURE:\n{json.dumps(arch, ensure_ascii=False)}\n\n"
        f"FAILING CHECKS:\n{failures}\n\nPROJECT FILES:\n{_context(files, 8000)}\n"
    )
    return parse_files(llm.complete("fixer", system, user))
