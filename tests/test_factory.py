import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from factory import agents, pipeline, quality
from factory.llm import LLMError, Mock, Ollama, get_llm


def approve(stage, run):
    return "approve", ""


def pause(stage, run):
    return "pause", ""


class BrokenCode(Mock):
    """Gera app.py quebrado; o fixer corrige (ou nao, conforme `fixes`)."""

    def __init__(self, fixes: bool):
        self.fixes = fixes
        self.fixer_calls = 0

    def complete(self, role, system, user):
        if role == "coder" and "FILE: app.py" in user:
            return "IDEA = 'x'\n\ndef describe(:\n    return IDEA\n"
        if role == "fixer":
            self.fixer_calls += 1
            if not self.fixes:
                return "=== FILE: app.py ===\nIDEA = 'x'\n\ndef describe(:\n    pass\n"
            return "=== FILE: app.py ===\n```python\nIDEA = 'x'\n\n\ndef describe():\n    return f'App: {IDEA}'\n```\n"
        return super().complete(role, system, user)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def new_run(self, llm):
        return pipeline.Run.create(self.home / "runs", "um app de teste", llm.name)


class TestUtils(unittest.TestCase):
    def test_safe_rel(self):
        for bad in ("/etc/passwd", "../x", "a/../../x", "", ".", ".git/config", ".venv/x", None):
            with self.assertRaises(agents.AgentError, msg=repr(bad)):
                agents.safe_rel(bad)
        self.assertEqual(agents.safe_rel("tests\\test_a.py"), "tests/test_a.py")

    def test_extract_json(self):
        self.assertEqual(agents.extract_json('texto ```json\n{"a": 1}\n``` fim'), {"a": 1})
        with self.assertRaises(agents.AgentError):
            agents.extract_json("sem json")
        with self.assertRaises(agents.AgentError):
            agents.extract_json("{quebrado")

    def test_strip_fence_only_whole_block(self):
        self.assertEqual(agents.strip_fence("```py\nx = 1\n```"), "x = 1\n")
        md = "# T\n\n```py\nx\n```\n\nfim"
        self.assertEqual(agents.strip_fence(md), md + "\n")

    def test_parse_files(self):
        out = agents.parse_files("=== FILE: a.py ===\nx=1\n=== FILE: d/b.py ===\n```\ny=2\n```\n")
        self.assertEqual(out, {"a.py": "x=1\n", "d/b.py": "y=2\n"})
        with self.assertRaises(agents.AgentError):
            agents.parse_files("=== FILE: ../evil ===\nx")
        with self.assertRaises(agents.AgentError):
            agents.parse_files("nada")

    def test_tests_executed(self):
        self.assertEqual(quality.tests_executed("Ran 3 tests in 0.1s\n\nOK"), 3)
        self.assertEqual(quality.tests_executed("1 failed, 4 passed in 1s"), 4)
        self.assertEqual(quality.tests_executed("Ran 0 tests in 0.0s"), 0)
        self.assertIsNone(quality.tests_executed("nada"))

    def test_architecture_validation(self):
        base = {"stack": {"language": "python"}, "files": [{"path": "a.py", "purpose": "p"}], "commands": {"test": "pytest"}}
        with self.assertRaises(agents.AgentError):  # sem arquivo de teste
            agents._validate_architecture(json.loads(json.dumps(base)))
        base["files"].append({"path": "../tests/test_a.py", "purpose": "p"})
        with self.assertRaises(agents.AgentError):  # caminho inseguro
            agents._validate_architecture(base)

    def test_get_llm(self):
        self.assertTrue(get_llm({}).is_mock)
        with self.assertRaises(LLMError):
            get_llm({"FACTORY_LLM": "anthropic"})
        with self.assertRaises(LLMError):
            get_llm({"FACTORY_LLM": "anthropic", "FACTORY_MODEL": "m"})
        with self.assertRaises(LLMError):
            get_llm({"FACTORY_LLM": "xyz", "FACTORY_MODEL": "m"})


class TestPipeline(Base):
    def test_end_to_end_mock_delivers(self):
        run = self.new_run(Mock())
        out = pipeline.execute(run, Mock(), approve)
        self.assertEqual(run.state["status"], "delivered")
        for f in ("app.py", "tests/test_app.py", "README.md", "MANIFEST.json", "DELIVERY_REPORT.md", ".git"):
            self.assertTrue((out / f).exists(), f)
        self.assertGreater((out / "app.py").stat().st_size, 0)
        report = (out / "DELIVERY_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("mock", report)
        self.assertIn("Nenhum modelo real", report)
        q = run.read("quality.json")
        self.assertTrue(q["passed"])
        self.assertEqual({c["name"]: c["status"] for c in q["final_checks"]}["tests"], "pass")

    def test_pause_then_resume_approves_each_gate(self):
        llm = Mock()
        run = self.new_run(llm)
        gates = []
        while True:
            try:
                pipeline.execute(run, llm, pause)
                break
            except pipeline.Paused as e:
                gates.append(str(e))
                self.assertEqual(run.state["status"], "awaiting_approval")
                run = pipeline.Run.load(self.home / "runs", run.state["id"])  # recarrega do disco
                run.state["approvals"][run.state["pending_gate"]] = True
        self.assertEqual(gates, ["analysis", "architecture", "quality"])
        self.assertEqual(run.state["status"], "delivered")

    def test_resume_delivered_run_is_noop(self):
        run = self.new_run(Mock())
        out = pipeline.execute(run, Mock(), approve)
        run = pipeline.Run.load(self.home / "runs", run.state["id"])
        self.assertEqual(pipeline.execute(run, Mock(), pause), out)
        self.assertEqual(run.state["status"], "delivered")

    def test_reject_stops_before_implementation(self):
        run = self.new_run(Mock())
        with self.assertRaises(pipeline.Rejected):
            pipeline.execute(run, Mock(), lambda s, r: ("reject", ""))
        self.assertEqual(run.state["status"], "rejected")
        self.assertFalse(run.workspace.exists())

    def test_revise_regenerates_with_feedback(self):
        seen = []

        class Spy(Mock):
            def complete(self, role, system, user):
                if role == "analyst":
                    seen.append("HUMAN FEEDBACK" in user)
                return super().complete(role, system, user)

        calls = iter([("revise", "mais simples"), ("approve", "")])
        run = self.new_run(Spy())
        pipeline.execute(run, Spy(), lambda s, r: next(calls) if s == "analysis" else ("approve", ""))
        self.assertEqual(seen, [False, True])

    def test_failing_quality_blocks_delivery(self):
        llm = BrokenCode(fixes=False)
        run = self.new_run(llm)
        with self.assertRaises(pipeline.Blocked):
            pipeline.execute(run, llm, approve, max_fixes=2)
        self.assertEqual(run.state["status"], "blocked")
        self.assertEqual(llm.fixer_calls, 2)
        self.assertFalse((self.home / "deliveries").exists())
        self.assertFalse(run.read("quality.json")["passed"])

    def test_fix_loop_recovers(self):
        llm = BrokenCode(fixes=True)
        run = self.new_run(llm)
        out = pipeline.execute(run, llm, approve)
        self.assertEqual(llm.fixer_calls, 1)
        q = run.read("quality.json")
        self.assertEqual(len(q["rounds"]), 2)
        self.assertEqual(q["rounds"][0]["fixed_files"], ["app.py"])
        self.assertIn("def describe():", (out / "app.py").read_text())

    def test_mock_cannot_fix_is_reported_not_hidden(self):
        class MockBroken(Mock):
            def complete(self, role, system, user):
                if role == "coder" and "FILE: app.py" in user:
                    return "def (:\n"
                return super().complete(role, system, user)

        run = self.new_run(MockBroken())
        with self.assertRaises(pipeline.Blocked):
            pipeline.execute(run, MockBroken(), approve)
        self.assertIn("mock nao sabe corrigir", run.read("quality.json")["rounds"][0]["fixer_error"])


class TestQuality(Base):
    def _ws(self, files):
        for rel, c in files.items():
            (self.home / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.home / rel).write_text(c)
        return self.home

    def test_disallowed_executable_is_not_run(self):
        marker = self.home / "pwned"
        ws = self._ws({"a.py": "x=1\n"})
        checks = quality.run_checks(ws, {"commands": {"test": f"touch {marker}"}}, install=False)
        self.assertEqual({c.name: c.status for c in checks}["tests"], "fail")
        self.assertFalse(marker.exists())

    def test_zero_tests_is_a_failure(self):
        ws = self._ws({"a.py": "x=1\n"})
        checks = quality.run_checks(ws, {"commands": {"test": "python -m unittest discover -s tests"}}, install=False)
        self.assertEqual({c.name: c.status for c in checks}["tests"], "fail")

    def test_exit_zero_without_tests_detected_fails(self):
        ws = self._ws({"a.py": "x=1\n"})
        checks = quality.run_checks(ws, {"commands": {"test": "python -c pass"}}, install=False)
        t = [c for c in checks if c.name == "tests"][0]
        self.assertEqual(t.status, "fail")
        self.assertIn("nao foi possivel confirmar", t.detail)

    def test_syntax_error_detected_without_executing(self):
        ws = self._ws({"a.py": "def (:\n"})
        checks = quality.run_checks(ws, {"commands": {"test": "python -V"}}, install=False)
        self.assertEqual(checks[0].status, "fail")


class _Fake(BaseHTTPRequestHandler):
    received = None

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).received = (self.path, body)
        out = json.dumps({"message": {"content": "resposta-fake"}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class TestOllamaAgainstFakeServer(unittest.TestCase):
    """Valida montagem de request/parse contra servidor FALSO; nao e um Ollama real."""

    def test_roundtrip(self):
        srv = HTTPServer(("127.0.0.1", 0), _Fake)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        out = Ollama("m", f"http://127.0.0.1:{srv.server_port}").complete("coder", "SYS", "USR")
        self.assertEqual(out, "resposta-fake")
        path, body = _Fake.received
        self.assertEqual(path, "/api/chat")
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        self.assertFalse(body["stream"])

    def test_connection_error_is_llmerror(self):
        with self.assertRaises(LLMError):
            Ollama("m", "http://127.0.0.1:1").complete("coder", "s", "u")


if __name__ == "__main__":
    unittest.main()
