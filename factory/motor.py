"""Motor: carrega a especificacao de fundamentos e regras universais (specs/fundamentals_and_rules.md).

Parser simples por cabecalhos Markdown (sem dependencias externas).
"""
from __future__ import annotations

import re
from pathlib import Path

SPEC_PATH = Path(__file__).resolve().parent.parent / "specs" / "fundamentals_and_rules.md"


class SpecError(RuntimeError):
    pass


def read_spec(path: Path = SPEC_PATH) -> str:
    if not path.is_file():
        raise SpecError(f"especificacao nao encontrada: {path}")
    return path.read_text(encoding="utf-8")


def _sections(text: str, level: int) -> dict[str, str]:
    """Mapeia titulo -> corpo para cabecalhos do nivel dado ('## ' = 2)."""
    mark = "#" * level + " "
    out: dict[str, list[str]] = {}
    cur = None
    for line in text.splitlines():
        if line.startswith(mark):
            cur = line[len(mark):].strip()
            out[cur] = []
        elif line.startswith("#" * (level - 1) + " ") and level > 1 and not line.startswith("#" * level):
            cur = None
        elif cur is not None:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def load_fundamentals(text: str | None = None) -> list[dict[str, str]]:
    """Linhas da tabela da secao '1. Fundamentos' -> [{area, description, example}]."""
    secs = _sections(text if text is not None else read_spec(), 2)
    key = next((k for k in secs if k.startswith("1.")), None)
    if key is None:
        return []
    rows = []
    for line in secs[key].splitlines():
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 3 or cols[0] in ("Área", "Area") or set(cols[0]) <= set("-: "):
            continue
        rows.append({"area": cols[0].strip("*"), "description": cols[1], "example": cols[2]})
    return rows


def load_rules(text: str | None = None) -> dict[str, str]:
    """Subsecoes '### 2.x ...' da secao 2 -> {titulo: corpo}."""
    return {k: v for k, v in _sections(text if text is not None else read_spec(), 3).items() if re.match(r"2\.\d", k)}


def validate_spec(text: str | None = None) -> None:
    """Falha (SpecError) se a especificacao nao tiver ao menos 1 fundamento e 6 regras."""
    text = text if text is not None else read_spec()
    if not load_fundamentals(text):
        raise SpecError("especificacao sem fundamentos (secao '1.' com tabela)")
    if len(load_rules(text)) < 6:
        raise SpecError("especificacao com menos de 6 regras (subsecoes '### 2.x')")
