"""CLI: factory run "ideia" | resume RUN_ID [--approve] | status [RUN_ID]

Codigos de saida: 0 entregue | 1 erro | 2 aguardando aprovacao | 3 rejeitado/bloqueado.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import agents, motor, pipeline
from .llm import LLMError, get_llm


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def _show(run: pipeline.Run, stage: str) -> None:
    art = {"analysis": "analysis.json", "architecture": "architecture.json", "quality": "quality.json"}[stage]
    print(f"\n=== Gate: {stage} ===  (artefato: {run.dir / art})")
    data = run.read(art)
    if stage == "analysis":
        print(data["summary"])
        for r in data["requirements"]:
            print(f"  {r['id']}: {r['text']}")
        for q in data.get("open_questions", []):
            print(f"  ? {q}")
    elif stage == "architecture":
        print(json.dumps(data["stack"], ensure_ascii=False))
        for f in data["files"]:
            print(f"  {f['path']}: {f['purpose']}")
    else:
        for c in data["final_checks"]:
            print(f"  [{c['status']}] {c['name']}")


def make_gate(auto: bool):
    def gate(stage: str, run: pipeline.Run):
        if auto:
            return "approve", ""
        if not sys.stdin.isatty():
            return "pause", ""
        _show(run, stage)
        ans = input("[a]provar / [r]evisar com feedback / [x] rejeitar > ").strip().lower()
        if ans.startswith("a"):
            return "approve", ""
        if ans.startswith("r"):
            return "revise", input("feedback > ")
        return "reject", ""
    return gate


def _drive(run: pipeline.Run, llm, args) -> int:
    try:
        out = pipeline.execute(run, llm, make_gate(args.auto_approve), not args.no_install, args.max_fixes)
    except pipeline.Paused as e:
        print(f"\nAguardando aprovacao do gate '{e}'. Revise os artefatos em {run.dir} e rode:\n"
              f"  python -m factory resume {run.state['id']} --approve")
        return 2
    except pipeline.Rejected as e:
        print(f"\nRejeitado no gate '{e}'. Run {run.state['id']} encerrado.")
        return 3
    except pipeline.Blocked as e:
        print(f"\nENTREGA BLOQUEADA: {e}\nRelatorio: {run.dir / 'quality.json'}")
        return 3
    except (agents.AgentError, LLMError, RuntimeError) as e:
        print(f"\nERRO ({type(e).__name__}): {e}", file=sys.stderr)
        run.log(f"erro: {type(e).__name__}: {e}")
        run.finish("error")
        return 1
    print(f"\nEntregue em: {out}\nRelatorio: {out / 'DELIVERY_REPORT.md'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path(".env"))
    ap = argparse.ArgumentParser(prog="factory", description="Fabrica de software controlada por agentes")
    ap.add_argument("--home", default="factory-data", help="pasta com runs/ e deliveries/")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "resume"):
        p = sub.add_parser(name)
        if name == "run":
            p.add_argument("idea")
        else:
            p.add_argument("run_id")
            p.add_argument("--approve", action="store_true", help="aprova o gate pendente")
        p.add_argument("--auto-approve", action="store_true", help="aprova todos os gates (sem humano)")
        p.add_argument("--no-install", action="store_true", help="nao instalar requirements.txt")
        p.add_argument("--max-fixes", type=int, default=2)
    st = sub.add_parser("status")
    st.add_argument("run_id", nargs="?")
    sub.add_parser("spec", help="valida e lista fundamentos e regras universais")
    wb = sub.add_parser("web", help="inicia servidor da interface web visual")
    wb.add_argument("--host", default="127.0.0.1")
    wb.add_argument("--port", type=int, default=8080)
    args = ap.parse_args(argv)
    runs = Path(args.home) / "runs"

    if args.cmd == "web":
        from . import web
        server = web.run_server(args.host, args.port, args.home)
        print(f"Interface Web da Software Factory ativa em: http://{args.host}:{args.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nEncerrando servidor web.")
            server.server_close()
        return 0

    if args.cmd == "spec":
        try:
            motor.validate_spec()
        except motor.SpecError as e:
            print(f"ERRO: {e}", file=sys.stderr)
            return 1
        print(f"Especificacao OK: {motor.SPEC_PATH}")
        for f in motor.load_fundamentals():
            print(f"  [fundamento] {f['area']}: {f['description']}")
        for name in motor.load_rules():
            print(f"  [regra] {name}")
        return 0

    if args.cmd == "status":
        if args.run_id:
            print(json.dumps(pipeline.Run.load(runs, args.run_id).state, ensure_ascii=False, indent=2))
        else:
            for d in sorted(runs.glob("*/state.json")) if runs.exists() else []:
                s = json.loads(d.read_text(encoding="utf-8"))
                print(f"{s['id']}\t{s['status']}\t{s['idea'][:60]}")
        return 0

    try:
        llm = get_llm()
    except LLMError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1
    if llm.is_mock:
        print("AVISO: provedor mock - nenhum modelo real sera consultado.", file=sys.stderr)

    if args.cmd == "run":
        run = pipeline.Run.create(runs, args.idea, llm.name)
    else:
        run = pipeline.Run.load(runs, args.run_id)
        gate = run.state.get("pending_gate")
        if args.approve and gate:
            run.state["approvals"][gate] = True
            run.save()
        elif args.approve:
            print("Nenhum gate pendente.", file=sys.stderr)
    print(f"Run: {run.state['id']}")
    if args.auto_approve:
        run.state["auto_approve"] = True
        run.save()
    return _drive(run, llm, args)


if __name__ == "__main__":
    sys.exit(main())
