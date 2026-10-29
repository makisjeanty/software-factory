"""Agente de qualidade: gates automaticos executados de verdade no workspace.

Nada aqui "assume" sucesso: cada check devolve pass/fail/skipped com detalhe,
e "testes passaram" exige que o runner tenha executado >= 1 teste.
"""
from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# Executaveis que o plano do arquitetura pode invocar. NAO e um sandbox.
ALLOWED = {"python", "python3", "pytest", "npm", "npx", "node", "go", "cargo", "php"}
TIMEOUT = 300


@dataclass
class Check:
    name: str
    status: str  # pass | fail | skipped
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def check_integrity(ws: Path) -> Check:
    """Verifica anomalias de integridade geradas por LLMs: duplicatas espúrias e arquivos trocados."""
    skip = {".git", ".venv", "__pycache__"}
    files = [p for p in ws.rglob("*") if p.is_file() and not (skip & set(p.parts))]

    # 1) Arquivos não vazios com conteúdo idêntico (ex.: requirements.txt copiado de app.py)
    seen_hashes: dict[str, Path] = {}
    dups: list[str] = []
    for f in sorted(files):
        try:
            content = f.read_bytes()
        except OSError as e:
            return Check("integrity", "fail", f"erro lendo {f.relative_to(ws)}: {e}")
        if not content.strip():
            continue  # arquivos vazios como __init__.py ou requirements.txt sem dependencias sao validos
        h = hashlib.sha256(content).hexdigest()
        if h in seen_hashes:
            dups.append(f"{f.relative_to(ws)} e {seen_hashes[h].relative_to(ws)} possuem conteudo identico")
        else:
            seen_hashes[h] = f

    if dups:
        return Check("integrity", "fail", "arquivos duplicados detectados:\n" + "\n".join(dups))

    # 2) requirements.txt nao pode conter codigo Python
    req = ws / "requirements.txt"
    if req.is_file():
        lines = [line.strip() for line in req.read_text(encoding="utf-8", errors="replace").splitlines()]
        for l in lines:
            if l.startswith(("def ", "class ", "import ", "from ", "return ")) or re.match(r"^[a-zA-Z_]\w*\s*\(", l):
                return Check("integrity", "fail", "requirements.txt contem codigo Python em vez de dependencias pip")

    # 3) README.md nao deve ser script de teste puro
    readme = ws / "README.md"
    if readme.is_file():
        first_lines = [l.strip() for l in readme.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()][:5]
        if any(l.startswith(("import unittest", "from ", "def test_")) for l in first_lines):
            return Check("integrity", "fail", "README.md contem script de teste em vez de documentacao Markdown")

    return Check("integrity", "pass", f"{len(files)} arquivo(s) verificados sem anomalias")


def venv_python(ws: Path) -> Path | None:
    for rel in ("bin/python", "Scripts/python.exe"):
        p = ws / ".venv" / rel
        if p.exists():
            return p
    return None


def _sh(argv: list[str], ws: Path) -> tuple[int | None, str]:
    vp = venv_python(ws)
    path = os.environ.get("PATH", "")
    if vp:
        path = f"{vp.parent}{os.pathsep}{path}"
    env = {"PATH": path, "HOME": str(ws), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        p = subprocess.run(argv, cwd=ws, env=env, capture_output=True, text=True, timeout=TIMEOUT)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return None, f"timeout after {TIMEOUT}s"
    except OSError as e:
        return None, f"cannot execute {argv[0]}: {e}"


def _tail(text: str, n: int = 4000) -> str:
    return text if len(text) <= n else "...\n" + text[-n:]


def tests_executed(output: str) -> int | None:
    """Numero de testes que o runner diz ter executado (None = nao detectado)."""
    counts = [int(x) for x in re.findall(r"Ran (\d+) tests?", output)]
    counts += [int(x) for x in re.findall(r"(\d+) passed", output)]
    return max(counts) if counts else None


def _py_files(ws: Path) -> list[Path]:
    return [p for p in ws.rglob("*.py") if ".venv" not in p.parts and "__pycache__" not in p.parts]


def run_checks(ws: Path, arch: dict, install: bool = True) -> list[Check]:
    checks: list[Check] = []

    # 0) integridade basica dos artefatos
    c_int = check_integrity(ws)
    checks.append(c_int)
    if c_int.status == "fail":
        return checks

    pys = _py_files(ws)

    # 1) sintaxe (compile em processo, sem executar o codigo)
    if pys:
        bad = []
        for p in pys:
            try:
                compile(p.read_text(encoding="utf-8"), str(p), "exec")
            except (SyntaxError, UnicodeDecodeError, ValueError) as e:
                bad.append(f"{p.relative_to(ws)}: {e}")
        checks.append(Check("syntax", "fail" if bad else "pass", "\n".join(bad) or f"{len(pys)} arquivo(s) .py compilam"))
    else:
        checks.append(Check("syntax", "skipped", "nenhum arquivo .py"))

    # 2) instalacao de dependencias (venv isolado no workspace)
    if (ws / "requirements.txt").exists():
        if not install:
            checks.append(Check("install", "skipped", "--no-install"))
        else:
            rc, out = _sh([sys.executable, "-m", "venv", ".venv"], ws)
            if rc == 0 and (vp := venv_python(ws)):
                rc, out = _sh([str(vp), "-m", "pip", "install", "-q", "-r", "requirements.txt"], ws)
            checks.append(Check("install", "pass" if rc == 0 else "fail", _tail(out, 1500)))
            if rc != 0:
                return checks

    # 3) testes (comando do arquiteto, so executaveis permitidos, sem shell)
    argv = shlex.split(arch["commands"]["test"])
    if not argv or argv[0] not in ALLOWED:
        checks.append(Check("tests", "fail", f"executavel nao permitido: {argv[:1]} (permitidos: {sorted(ALLOWED)})"))
    else:
        if argv[0] in ("python", "python3"):
            argv[0] = str(venv_python(ws) or sys.executable)
        rc, out = _sh(argv, ws)
        n = tests_executed(out)
        if rc != 0:
            checks.append(Check("tests", "fail", _tail(out)))
        elif not n:
            checks.append(Check("tests", "fail", "exit 0 mas nao foi possivel confirmar >= 1 teste executado\n" + _tail(out, 1500)))
        else:
            checks.append(Check("tests", "pass", f"{n} teste(s) executado(s), exit 0"))

    # 4) lint / seguranca: so se a ferramenta existir; senao, "skipped" explicito
    if pys:
        for name, argv in (("lint (ruff)", ["ruff", "check", "."]), ("security (bandit)", ["bandit", "-q", "-r", ".", "-x", "./.venv,./tests"])):
            if not shutil.which(argv[0]):
                checks.append(Check(name, "skipped", f"{argv[0]} nao instalado"))
                continue
            rc, out = _sh(argv, ws)
            checks.append(Check(name, "pass" if rc == 0 else "fail", _tail(out, 3000) if rc != 0 else "sem achados"))
    return checks


def passed(checks: list[Check]) -> bool:
    return not any(c.status == "fail" for c in checks)


def format_failures(checks: list[Check]) -> str:
    return "\n\n".join(f"[{c.name}] {c.status}\n{c.detail}" for c in checks if c.status == "fail")
