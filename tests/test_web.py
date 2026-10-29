import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from factory import web


class WebInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.home = Path(cls._tmp.name)
        (cls.home / "runs").mkdir()

        # Cria uma run fake para testar
        rdir = cls.home / "runs" / "20261029-test-run"
        rdir.mkdir()
        (rdir / "state.json").write_text(json.dumps({
            "id": "20261029-test-run",
            "idea": "teste web",
            "status": "delivered",
            "done": ["analysis", "architecture", "implementation", "quality", "delivery"]
        }), encoding="utf-8")

        # Inicia servidor em porta 0 (efêmera automática do SO)
        cls.server = web.run_server("127.0.0.1", 0, str(cls.home))
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def _get(self, path: str):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.headers.get_content_type(), resp.read()

    def test_root_serves_html(self):
        status, ctype, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "text/html")
        self.assertIn(b"Software Factory", body)
        self.assertIn(b"Autonomous Pipeline", body)

    def test_api_runs_list(self):
        status, ctype, body = self._get("/api/runs")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "application/json")
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], "20261029-test-run")

    def test_api_run_details(self):
        status, ctype, body = self._get("/api/runs/20261029-test-run")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["state"]["id"], "20261029-test-run")

    def test_api_spec(self):
        status, ctype, body = self._get("/api/spec")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("fundamentals", data)
        self.assertIn("rules", data)
        self.assertGreaterEqual(len(data["fundamentals"]), 1)


if __name__ == "__main__":
    unittest.main()
