# Software Factory

Ideia em linguagem natural → app entregue, via pipeline controlado:

```
ideia → [Analista] → GATE → [Arquiteto] → GATE → [Coder + Tester] → [QA + Fixer] → GATE → Entrega
```

| Etapa | O que faz | Artefato |
|---|---|---|
| Analista | requisitos, critérios de aceite, perguntas em aberto | `analysis.json` |
| Arquiteto | stack, plano de arquivos, comando de teste, riscos | `architecture.json` |
| Coder/Tester | escreve 1 arquivo por chamada (código antes, testes depois) | `workspace/` |
| QA | sintaxe, install em venv, **testes (exige ≥1 executado)**, ruff/bandit se instalados; falha → agente Fixer (até `--max-fixes`) | `quality.json` |
| Entrega | cópia limpa, `MANIFEST.json` (sha256), `git init` + commit, `DELIVERY_REPORT.md` | `factory-data/deliveries/<id>/` |

Gates humanos: após análise, arquitetura e qualidade. QA reprovado **bloqueia** a entrega (exit 3).

## Uso

```bash
cp .env.example .env            # escolha FACTORY_LLM / FACTORY_MODEL / chave
python3 -m factory run "sistema de controle de estoque"        # gates interativos (TTY)
python3 -m factory run "..." </dev/null                         # sem TTY: pausa no 1º gate (exit 2)
python3 -m factory resume <RUN_ID> --approve                    # aprova o gate pendente e segue
python3 -m factory run "..." --auto-approve                     # sem humano (relatório declara isso)
python3 -m factory status [RUN_ID]
```

No gate interativo: `a` aprova, `r` pede revisão com feedback (regenera a etapa), `x` rejeita.

Exit codes: `0` entregue · `1` erro · `2` aguardando aprovação · `3` rejeitado/bloqueado.

## Provedores (`FACTORY_LLM`)

`mock` (padrão; **não é um modelo** — esqueleto fixo para testar o pipeline), `ollama`, `openai` (qualquer endpoint compatível via `OPENAI_BASE_URL`), `anthropic`, `gemini`. Provedores reais exigem `FACTORY_MODEL` (sem default de propósito: nomes de modelo mudam).

## Testes

```bash
python3 -m unittest discover -s tests -t . -v
```

## Limitações conhecidas

- O QA executa o comando de teste que o LLM planejou, com allowlist de executáveis, sem shell e env mínimo — **isso não é um sandbox**. `npm test`/`pip install` rodam código/pacotes escolhidos pelo modelo. Para uso sério, rode a fábrica dentro de container/VM.
- Provedores `openai`, `anthropic`, `gemini` e `ollama` real **não foram testados** contra os serviços reais (só o formato de request do Ollama contra um servidor falso local).
- Qualidade final do código depende do modelo; os gates só provam que o build/testes passam, não que a ideia foi atendida.
