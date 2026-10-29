"""Pipeline: ideia -> analise -> arquitetura -> implementacao -> qualidade -> entrega.

Estado persistido em runs/<id>/state.json; cada estagio grava um artefato.
Gates humanos apos analise, arquitetura e qualidade. Pausar e retomar e
suportado: `resume --approve` aprova o gate pendente.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from . import agents, delivery, motor, quality

GATES = ("analysis", "architecture", "quality")


class Paused(Exception):
    """Aguardando aprovacao humana."""


class Rejected(Exception):
    pass


class Blocked(Exception):
    """Gate de qualidade falhou: entrega bloqueada."""


class Run:
    def __init__(self, path: Path):
        self.dir = path
        self.state = json.loads((path / "state.json").read_text(encoding="utf-8"))

    @classmethod
    def create(cls, runs_dir: Path, idea: str, llm_name: str) -> "Run":
        slug = re.sub(r"[^a-z0-9]+", "-", idea.lower()).strip("-")[:30] or "run"
        rid = f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}"
        d = runs_dir / rid
        d.mkdir(parents=True)
        run = cls.__new__(cls)
        run.dir = d
        run.state = {"id": rid, "idea": idea, "llm": llm_name, "status": "running", "done": [], "approvals": {}, "log": []}
        run.save()
        return run

    @classmethod
    def load(cls, runs_dir: Path, rid: str) -> "Run":
        return cls(runs_dir / rid)

    @property
    def workspace(self) -> Path:
        return self.dir / "workspace"

    def save(self) -> None:
        (self.dir / "state.json").write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def log(self, msg: str) -> None:
        self.state["log"].append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "msg": msg})
        self.save()

    def write(self, name: str, obj) -> None:
        (self.dir / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    def read(self, name: str):
        return json.loads((self.dir / name).read_text(encoding="utf-8"))

    def exists(self, name: str) -> bool:
        return (self.dir / name).exists()

    def finish(self, status: str) -> None:
        self.state["status"] = status
        self.save()


def _gate(run: Run, gate, stage: str, revisable: bool):
    """Devolve ('approve'|'revise', feedback) ou levanta Paused/Rejected."""
    if run.state["approvals"].pop(stage, False):  # aprovado via `resume --approve`
        run.save()
        return "approve", ""
    kind, feedback = gate(stage, run)
    if kind == "revise" and not (revisable and feedback.strip()):
        kind = "reject"
    if kind == "pause":
        run.state.update(status="awaiting_approval", pending_gate=stage)
        run.save()
        raise Paused(stage)
    if kind == "reject":
        run.finish("rejected")
        raise Rejected(stage)
    return kind, feedback


def _stage(run: Run, gate, stage: str, artifact: str, produce, revisable: bool = True):
    if stage in run.state["done"]:
        return run.read(artifact)
    feedback = ""
    while True:
        if feedback or not run.exists(artifact):
            run.log(f"{stage}: gerando" + (" (com feedback humano)" if feedback else ""))
            run.write(artifact, produce(feedback))
        if stage == "quality" and not run.read(artifact)["passed"]:
            run.finish("blocked")
            raise Blocked("checks de qualidade falharam; veja quality.json")
        kind, feedback = _gate(run, gate, stage, revisable)
        if kind == "approve":
            run.state["done"].append(stage)
            run.state.pop("pending_gate", None)
            run.log(f"{stage}: aprovado")
            return run.read(artifact)


def _implement(run: Run, llm, analysis: dict, arch: dict) -> None:
    ws = run.workspace
    existing: dict[str, str] = {}
    for spec in agents.implement_order(arch):
        run.log(f"implementacao: {spec['path']}")
        content = agents.write_file(llm, analysis, arch, spec, existing)
        existing[spec["path"]] = content
        target = ws / spec["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _read_workspace(ws: Path) -> dict[str, str]:
    skip = {".venv", "__pycache__", ".git"}
    return {
        str(p.relative_to(ws)): p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(ws.rglob("*"))
        if p.is_file() and not skip & set(p.relative_to(ws).parts)
    }


def _quality_loop(run: Run, llm, analysis: dict, arch: dict, install: bool, max_fixes: int) -> dict:
    ws, attempts = run.workspace, []
    for i in range(max_fixes + 1):
        run.log(f"qualidade: rodada {i + 1}")
        checks = quality.run_checks(ws, arch, install)
        attempts.append({"round": i + 1, "checks": [c.to_dict() for c in checks]})
        if quality.passed(checks) or i == max_fixes:
            break
        try:
            changed = agents.fix(llm, analysis, arch, _read_workspace(ws), quality.format_failures(checks))
        except (agents.AgentError, Exception) as e:  # LLM indisponivel/sem correcao: registrar e parar
            attempts[-1]["fixer_error"] = f"{type(e).__name__}: {e}"
            break
        for rel, content in changed.items():
            (ws / rel).parent.mkdir(parents=True, exist_ok=True)
            (ws / rel).write_text(content, encoding="utf-8")
        attempts[-1]["fixed_files"] = sorted(changed)
    return {"passed": quality.passed(checks), "rounds": attempts, "final_checks": [c.to_dict() for c in checks]}


def execute(run: Run, llm, gate, install: bool = True, max_fixes: int = 2) -> Path:
    """Executa/retoma o pipeline. Levanta Paused, Rejected ou Blocked."""
    s = run.state
    if "delivery" in s["done"]:
        return Path(s["delivery"])
    motor.validate_spec()  # regra: fundamentos e regras universais devem existir antes de gerar codigo
    s["spec"] = {"fundamentals": len(motor.load_fundamentals()), "rules": len(motor.load_rules())}
    s["status"] = "running"
    run.save()
    analysis = _stage(run, gate, "analysis", "analysis.json", lambda fb: agents.analyze(llm, s["idea"], fb))
    arch = _stage(run, gate, "architecture", "architecture.json", lambda fb: agents.design(llm, analysis, fb))
    if "implementation" not in s["done"]:
        _implement(run, llm, analysis, arch)
        s["done"].append("implementation")
        run.log("implementacao: concluida")
    _stage(
        run, gate, "quality", "quality.json",
        lambda fb: _quality_loop(run, llm, analysis, arch, install, max_fixes), revisable=False,
    )
    out = delivery.deliver(run, analysis, arch)
    s["done"].append("delivery")
    s["delivery"] = str(out)
    run.finish("delivered")
    run.log(f"entrega: {out}")
    return out
