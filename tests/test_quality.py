import tempfile
import unittest
from pathlib import Path

from factory.quality import check_integrity, run_checks


class QualityIntegrityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_clean_workspace_passes(self):
        (self.ws / "app.py").write_text("def run(): return 42\n", encoding="utf-8")
        (self.ws / "README.md").write_text("# Meu Projeto\nDescricao\n", encoding="utf-8")
        (self.ws / "requirements.txt").write_text("pytest>=7.0\n", encoding="utf-8")
        chk = check_integrity(self.ws)
        self.assertEqual(chk.status, "pass")

    def test_empty_files_allowed(self):
        (self.ws / "__init__.py").write_text("", encoding="utf-8")
        (self.ws / "requirements.txt").write_text("", encoding="utf-8")
        chk = check_integrity(self.ws)
        self.assertEqual(chk.status, "pass")

    def test_duplicate_non_empty_files_fail(self):
        code = "def foo(): return 1\n"
        (self.ws / "app.py").write_text(code, encoding="utf-8")
        (self.ws / "requirements.txt").write_text(code, encoding="utf-8")
        chk = check_integrity(self.ws)
        self.assertEqual(chk.status, "fail")
        self.assertIn("arquivos duplicados", chk.detail)

    def test_requirements_with_python_code_fails(self):
        (self.ws / "requirements.txt").write_text("import os\ndef imc(p, a):\n    return p / (a * a)\n", encoding="utf-8")
        chk = check_integrity(self.ws)
        self.assertEqual(chk.status, "fail")
        self.assertIn("requirements.txt contem codigo Python", chk.detail)

    def test_readme_with_test_script_fails(self):
        (self.ws / "README.md").write_text("import unittest\nclass TestApp(unittest.TestCase): pass\n", encoding="utf-8")
        chk = check_integrity(self.ws)
        self.assertEqual(chk.status, "fail")
        self.assertIn("README.md contem script de teste", chk.detail)

    def test_run_checks_blocks_early_on_integrity_fail(self):
        code = "def duplicate(): pass\n"
        (self.ws / "a.py").write_text(code, encoding="utf-8")
        (self.ws / "b.py").write_text(code, encoding="utf-8")
        arch = {"commands": {"test": "python -m unittest"}}
        checks = run_checks(self.ws, arch)
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].name, "integrity")
        self.assertEqual(checks[0].status, "fail")


if __name__ == "__main__":
    unittest.main()
