import json
import unittest
from pathlib import Path

CONTRACT = Path(__file__).resolve().parent.parent / "specs" / "contracts" / "create_user.schema.json"


class InvalidEmail(ValueError): ...
class WeakPassword(ValueError): ...


def create_user(email: str, pwd: str) -> dict:  # adaptador Python (espelha ADAPTERS.md)
    if "@" not in email:
        raise InvalidEmail(email)
    if len(pwd) < 8:
        raise WeakPassword()
    return {"id": 1, "status": "active"}


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.c = json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_contract_shape(self):
        self.assertEqual(set(self.c["properties"]), {"input", "output", "errors"})
        self.assertEqual(self.c["properties"]["input"]["required"], ["email", "pwd"])

    def test_python_adapter_matches_output_contract(self):
        out = create_user("a@b.c", "12345678")
        spec = self.c["properties"]["output"]
        self.assertEqual(set(out), set(spec["required"]))
        self.assertIn(out["status"], spec["properties"]["status"]["enum"])

    def test_python_adapter_declared_errors(self):
        declared = set(self.c["properties"]["errors"]["items"]["enum"])
        for exc, args in ((InvalidEmail, ("x", "12345678")), (WeakPassword, ("a@b.c", "1"))):
            with self.assertRaises(exc):
                create_user(*args)
            self.assertIn(exc.__name__, declared)


if __name__ == "__main__":
    unittest.main()
