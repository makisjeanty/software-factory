"""Servidor HTTP e API da Interface Web da Software Factory.

Implementado exclusivamente com bibliotecas padrao do Python (http.server).
"""
from __future__ import annotations

import json
import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import motor, pipeline
from .llm import LLMError, get_llm


def _read_json(path: Path) -> dict | None:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


class FactoryWebHandler(BaseHTTPRequestHandler):
    home_dir: Path = Path("factory-data")

    def _send_json(self, data: object, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _send_html(self, content: str, status: int = 200) -> None:
        payload = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def log_message(self, fmt, *args):
        # Desativa logs verbosos no stdout normal para manter o console limpo
        pass

    def do_HEAD(self):
        self.do_GET()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        path = unquote(url.path).rstrip("/")

        # Servir Interface Web SPA
        if path in ("", "/index.html"):
            ui_path = Path(__file__).parent / "web_ui.html"
            if ui_path.is_file():
                self._send_html(ui_path.read_text(encoding="utf-8"))
            else:
                self._send_html("<h1>Erro: factory/web_ui.html nao encontrado</h1>", 404)
            return

        # API: Lista de Runs
        if path == "/api/runs":
            runs_dir = self.home_dir / "runs"
            out = []
            if runs_dir.is_dir():
                for state_file in sorted(runs_dir.glob("*/state.json"), reverse=True):
                    s = _read_json(state_file)
                    if s:
                        out.append(s)
            self._send_json(out)
            return

        # API: Detalhes de um Run
        if path.startswith("/api/runs/"):
            run_id = path[len("/api/runs/"):]
            r_dir = self.home_dir / "runs" / run_id
            if not r_dir.is_dir() or not (r_dir / "state.json").is_file():
                self._send_json({"error": "run nao encontrado"}, 404)
                return

            state = _read_json(r_dir / "state.json")
            analysis = _read_json(r_dir / "analysis.json")
            arch = _read_json(r_dir / "architecture.json")
            quality_data = _read_json(r_dir / "quality.json")

            # Arquivos gerados
            files = {}
            target_dir = r_dir / "workspace"
            if state and state.get("delivery") and Path(state["delivery"]).is_dir():
                target_dir = Path(state["delivery"])

            if target_dir.is_dir():
                for p in sorted(target_dir.rglob("*")):
                    if p.is_file() and not {".git", ".venv", "__pycache__"} & set(p.parts):
                        try:
                            rel = str(p.relative_to(target_dir))
                            files[rel] = p.read_text(encoding="utf-8", errors="replace")
                        except Exception:
                            pass

            delivery_report = None
            if (target_dir / "DELIVERY_REPORT.md").is_file():
                delivery_report = (target_dir / "DELIVERY_REPORT.md").read_text(encoding="utf-8", errors="replace")

            self._send_json({
                "state": state,
                "analysis": analysis,
                "architecture": arch,
                "quality": quality_data,
                "files": files,
                "delivery_report": delivery_report
            })
            return

        # API: Especificação / Fundamentos
        if path == "/api/spec":
            try:
                self._send_json({
                    "fundamentals": motor.load_fundamentals(),
                    "rules": motor.load_rules()
                })
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return

        self._send_json({"error": "rota nao encontrada"}, 404)

    def do_POST(self):
        url = urlparse(self.path)
        path = unquote(url.path).rstrip("/")

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            payload = {}

        # API: Criar e disparar novo Run
        if path == "/api/runs":
            idea = payload.get("idea", "").strip()
            if not idea:
                self._send_json({"error": "ideia obrigatoria"}, 400)
                return

            auto_approve = bool(payload.get("auto_approve", True))
            no_install = bool(payload.get("no_install", True))

            try:
                llm = get_llm()
            except LLMError as e:
                self._send_json({"error": f"LLM error: {e}"}, 500)
                return

            runs_dir = self.home_dir / "runs"
            run = pipeline.Run.create(runs_dir, idea, llm.name)
            if auto_approve:
                run.state["auto_approve"] = True
                run.save()

            def _background_run():
                def _gate(stage: str, r: pipeline.Run):
                    return ("approve", "") if auto_approve else ("pause", "")

                try:
                    pipeline.execute(run, llm, _gate, install=not no_install, max_fixes=2)
                except pipeline.Paused:
                    pass
                except Exception as ex:
                    run.log(f"erro web: {ex}")
                    run.finish("error")

            thread = threading.Thread(target=_background_run, daemon=True)
            thread.start()

            self._send_json({"status": "started", "run_id": run.state["id"]}, 201)
            return

        # API: Aprovar Gate pendente
        if path.endswith("/approve") and path.startswith("/api/runs/"):
            run_id = path[len("/api/runs/"):-len("/approve")]
            r_dir = self.home_dir / "runs" / run_id
            if not r_dir.is_dir() or not (r_dir / "state.json").is_file():
                self._send_json({"error": "run nao encontrado"}, 404)
                return

            run = pipeline.Run.load(self.home_dir / "runs", run_id)
            gate = run.state.get("pending_gate")
            if gate:
                run.state["approvals"][gate] = True
                run.save()

                # Retomar em background
                try:
                    llm = get_llm()

                    def _resume():
                        try:
                            pipeline.execute(run, llm, lambda stg, r: ("approve", ""), install=True)
                        except Exception:
                            pass

                    threading.Thread(target=_resume, daemon=True).start()
                except Exception as e:
                    self._send_json({"error": str(e)}, 500)
                    return

                self._send_json({"status": "approved", "gate": gate})
            else:
                self._send_json({"error": "nenhum gate pendente"}, 400)
            return

        self._send_json({"error": "rota nao encontrada"}, 404)


def run_server(host: str = "127.0.0.1", port: int = 8080, home: str = "factory-data") -> ThreadingHTTPServer:
    FactoryWebHandler.home_dir = Path(home)
    server = ThreadingHTTPServer((host, port), FactoryWebHandler)
    return server
