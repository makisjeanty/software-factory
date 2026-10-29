import tempfile
import unittest
from pathlib import Path

from factory import motor

GOOD = """# T
## 1. Fundamentos (Camada 1)

| Área | Descrição | Exemplo |
|------|-----------|---------|
| **Lógica** | passo a passo | algoritmo |

## 2. Regras
### 2.1 A
a
### 2.2 B
b
### 2.3 C
c
### 2.4 D
d
### 2.5 E
e
### 2.6 F
f
## 3. Outro
"""


class MotorTests(unittest.TestCase):
    def test_real_spec_valid(self):
        motor.validate_spec()
        self.assertEqual(len(motor.load_fundamentals()), 5)
        self.assertEqual(len(motor.load_rules()), 6)

    def test_parse_sample(self):
        f = motor.load_fundamentals(GOOD)
        self.assertEqual(f, [{"area": "Lógica", "description": "passo a passo", "example": "algoritmo"}])
        self.assertEqual(list(motor.load_rules(GOOD))[0], "2.1 A")

    def test_rules_do_not_leak_into_next_section(self):
        self.assertEqual(motor.load_rules(GOOD)["2.6 F"], "f")

    def test_missing_rules_rejected(self):
        with self.assertRaises(motor.SpecError):
            motor.validate_spec(GOOD.replace("### 2.6 F\nf\n", ""))

    def test_missing_fundamentals_rejected(self):
        with self.assertRaises(motor.SpecError):
            motor.validate_spec("## 2. Regras\n### 2.1 A\na\n")

    def test_missing_file(self):
        with self.assertRaises(motor.SpecError):
            motor.read_spec(Path(tempfile.gettempdir()) / "nao-existe-spec.md")


if __name__ == "__main__":
    unittest.main()
