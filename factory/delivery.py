"""Entrega controlada: copia limpa, manifesto com hashes, repositorio git e relatorio."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

SKIP = shutil.ignore_patterns(".venv", "__pycache__", ".git", "*.pyc")


def _git(args: list[str], cwd: Path) -> tuple[bool, str]:
    if not shutil.which("git"):
        return False, "git nao instalado"
    p = subprocess.run(
        ["git", "-c", "user.name=software-factory", "-c", "user.email=factory@localhost", *args],
        cwd=cwd, capture_output=True, text=True,
    )
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def report(run, analysis: dict, arch: dict, q: dict, git_note: str) -> str:
    s = run.state
    mock = "mock" in s["llm"]
    lines = [
        f"# Relatorio de entrega - {s['id']}",
        "",
        f"**Ideia:** {s['idea']}",
        f"**LLM:** {s['llm']}",
    ]
    if mock:
        lines += ["", "> **ATENCAO:** gerado pelo provedor `mock`. Nenhum modelo real foi consultado; "
                  "o codigo e um esqueleto fixo, nao uma implementacao da ideia."]
    lines += ["", "## Requisitos"] + [f"- **{r['id']}** ({r.get('priority', '-')}): {r['text']}" for r in analysis["requirements"]]
    lines += ["", "## Criterios de aceite"] + [f"- {c}" for c in analysis["acceptance_criteria"]]
    if analysis.get("open_questions"):
        lines += ["", "## Perguntas em aberto"] + [f"- {c}" for c in analysis["open_questions"]]
    lines += ["", "## Arquitetura", f"- Stack: `{json.dumps(arch['stack'], ensure_ascii=False)}`", f"- Comando de teste: `{arch['commands']['test']}`", "- Arquivos:"]
    lines += [f"  - `{f['path']}` - {f['purpose']}" for f in arch["files"]]
    if arch.get("risks"):
        lines += ["- Riscos:"] + [f"  - {r}" for r in arch["risks"]]
    lines += ["", f"## Qualidade ({len(q['rounds'])} rodada(s))", "", "| Check | Status | Detalhe |", "|---|---|---|"]
    for c in q["final_checks"]:
        lines.append(f"| {c['name']} | {c['status']} | {c['detail'].splitlines()[0][:120] if c['detail'] else ''} |")
    skipped = [c["name"] for c in q["final_checks"] if c["status"] == "skipped"]
    if skipped:
        lines += ["", f"**Nao verificado (skipped):** {', '.join(skipped)}"]
    approvals = ("- **AUTO-APROVADO** (--auto-approve): nenhum humano revisou analise, arquitetura ou qualidade"
                 if s.get("auto_approve") else "- analise, arquitetura e qualidade aprovadas por humano antes da entrega")
    lines += ["", "## Aprovacoes humanas", approvals, "", f"## Git\n{git_note}", ""]
    return "\n".join(lines)


def deliver(run, analysis: dict, arch: dict) -> Path:
    q = run.read("quality.json")
    if not q["passed"]:  # defesa em profundidade; o pipeline ja bloqueia antes
        raise RuntimeError("entrega bloqueada: qualidade nao passou")
    out = run.dir.parent.parent / "deliveries" / run.state["id"]
    if out.exists():
        raise RuntimeError(f"destino ja existe: {out}")
    shutil.copytree(run.workspace, out, ignore=SKIP)
    manifest = {
        str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(out.rglob("*")) if p.is_file()
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    ok, msg = _git(["init", "-q", "-b", "main"], out)
    note = "repositorio inicializado em `main` com 1 commit" if ok else f"git indisponivel: {msg}"
    (out / "DELIVERY_REPORT.md").write_text(report(run, analysis, arch, q, note), encoding="utf-8")
    if ok:
        _git(["add", "-A"], out)
        ok, msg = _git(["commit", "-q", "-m", f"feat: entrega {run.state['id']}"], out)
        if not ok:
            (out / "DELIVERY_REPORT.md").write_text(report(run, analysis, arch, q, f"commit falhou: {msg}"), encoding="utf-8")
    return out
