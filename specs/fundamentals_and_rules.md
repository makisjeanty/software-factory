# Fundamentos e Regras Universais da Software Factory

---
## 1. Fundamentos (Camada 1)

| Área | Descrição | Exemplo de Aplicação |
|------|-----------|----------------------|
| **Lógica** | Definir passo‑a‑passo a solução do problema. | Algoritmo de cálculo de prazo de entrega. |
| **Dados** | Modelar estruturas (tipos, validações, relações). | `Order{id:int, amount:float, status:str}` |
| **Algoritmos** | Transformar regras de negócio em código executável. | Função `calculate_discount(order)` |
| **Abstração** | Ocultar detalhes irrelevantes, focar no modelo. | Interface `PaymentGateway` com método `process(payment)`. |
| **Decomposição** | Quebrar problema em sub‑tarefas menores e independentes. | Dividir “registro de cliente” em validação, persistência e notificação. |

---
## 2. Regras e Contratos (Camada 2)

### 2.1 Requisitos antes do código
- Cada módulo deve ter **Objetivo**, **Critério de Aceitação** e **Escopo** definidos.
- _Exemplo_: `module: user registration` – objetivo: criar conta; aceitação: email enviado e usuário ativo.

### 2.2 Contratos explícitos
- Definir **Entradas**, **Saídas**, **Condições de Erro**.
- Sintaxe recomendada (JSON‑Schema ou TypeScript‑like):
```json
{
  "name": "create_user",
  "input": {"type": "object", "properties": {"email": {"type": "string"}, "pwd": {"type": "string"}}, "required": ["email","pwd"]},
  "output": {"type": "object", "properties": {"id": {"type": "integer"}, "status": {"type": "string"}}},
  "errors": ["InvalidEmail", "WeakPassword"]
}
```

### 2.3 Evidência de Execução
- Cada teste deve gerar **relatório JSON** com `test_id`, `status`, `duration`, `log`.
- O motor salva o relatório em `workspace/quality/<run_id>/report.json`.

### 2.4 Segurança desde o início
- **Validação de entrada** (whitelisting).
- **Proteção de segredos** – usar variáveis de ambiente, nunca hard‑code.
- **Permissões** – princípio do menor privilégio nas chamadas externas.

### 2.5 Falhas controladas
- Em caso de erro: **log estruturado**, **artefato preservado**, **estado de retomada** registrado.
- Não é permitido “silenciar” falhas apenas para avançar (exit 3).

### 2.6 Complexidade proporcional
- Avaliar *Complexity Score* (linhas de código, número de dependências).<br>Regra: **Score ≤ 30** para scripts ≤ 200 LOC; caso contrário, refatorar.

---
## 3. Exemplos de Contratos e Casos de Falha

### 3.1 Contrato de API REST (Python – FastAPI)
```python
from pydantic import BaseModel

class CreateOrderReq(BaseModel):
    client_id: int
    items: list[int]

class CreateOrderRes(BaseModel):
    order_id: int
    status: str = "created"
```
**Falha esperada**: `HTTP 400` se `items` vazio.

### 3.2 Contrato de Função utilitária (PHP)
```php
/**
 * @param array{price:float, qty:int} $item
 * @return float
 */
function calculateLineTotal(array $item): float {
    if ($item['qty'] <= 0) throw new InvalidArgumentException('qty must be > 0');
    return $item['price'] * $item['qty'];
}
```
**Caso de falha**: `InvalidArgumentException` – deve ser capturado pelo caller e transformado em resposta de erro.

---
## 4. Critérios Verificáveis (KPIs)

| Critério | Métrica | Meta mínima |
|----------|---------|--------------|
| **Cobertura de testes** | % de linhas cobertas | ≥ 80 % |
| **Tempo de build** | segundos | ≤ 30 s |
| **Taxa de falhas por run** | falhas / run | ≤ 0.1 |
| **Segurança** | número de vulnerabilidades críticas (Bandit, PHPStan) | 0 |
| **Complexidade** | cyclomatic complexity média | ≤ 10 |

---
## 5. Como usar este artefato
1. **Importar** este documento na fase de *Fundamentos* do pipeline (`factory/pipeline.py`).
2. Os agentes (`analyze`, `design`) devem validar que suas saídas atendem a cada regra acima.
3. O *gate de qualidade* (`factory/quality.py`) consumirá os KPIs para decidir se o build pode avançar.

---
**Próximo passo**: implementar o *motor de fluxo* que lê esta especificação e cria dinamicamente os contratos/validações para cada projeto.
