# Ollama Telegram — Contrastive In-Context Learning Bot

Bot do Telegram com **Contrastive RAG** (Two-Stage Retrieval) sobre Ollama
local, indexado em FAISS, metadados ricos em SQLite, suporte a imagens
(Vision), transcrição de áudio (Whisper via `aiohttp`), tool calling, agentes
roteados por intenção/tags, configurações dinâmicas por usuário e **pipeline
totalmente observável** etapa-a-etapa.

## Estrutura

```
core/
  config.py             # Pydantic-Settings (.env)
  logger.py             # console colorido + RotatingFile
  exceptions.py         # hierarquia BotError
  audio_transcriber.py  # Whisper via aiohttp puro (sem SDK openai)
  pipeline.py           # PipelineRecorder com .step() context manager
database/
  sqlite_mgr.py         # interactions (rica), user_settings, pipeline_steps
  faiss_mgr.py          # IndexIDMap2/IndexFlatIP persistido (cosseno)
llm/
  ollama_client.py      # /api/chat, /api/embeddings, /api/tags, health_check
  intent_classifier.py  # 1 rótulo de set fechado (ALLOWED_INTENTS)
  tag_generator.py      # 1..3 tags livres em snake_case
  contrastive_rag.py    # Two-Stage + fallback neutro + retorno de IDs
  prompt_templates.py   # render_contrastive_prompt / render_neutral_context
tools/
  registry.py           # ToolRegistry async com despacho por nome
  web_search.py         # mock pronto para virar SerpAPI/Tavily
agents/
  router.py             # AgentRouter — tag/intent → AgentRoute (esboço)
tg/
  bot.py                # Application + DI (BotDependencies)
  handlers.py           # /start /help /config /stats /recall /history
                        # /ping /whoami /reset + texto/foto/doc/voz
  callbacks.py          # rate:* e cfg:*
tests/
  conftest.py           # fixtures + FakeOllama
  test_*.py             # cobertura por módulo
main.py                 # bootstrap async + health-check
requirements.txt
requirements-dev.txt
pytest.ini
.env.example
```

> **Por que `tg/` em vez de `telegram/`?** Um diretório local chamado
> `telegram` é resolvido pelo Python antes do pacote instalado pela
> `python-telegram-bot`, quebrando imports tipo `from telegram import …`.

## Pré-requisitos

1. **Python 3.11+** (3.12 testado)
2. **Ollama** rodando localmente — https://ollama.com
3. Modelos baixados:
   ```bash
   ollama pull gemma:2b           # chat (ou outro: llama3, qwen2, llava…)
   ollama pull nomic-embed-text   # embeddings (dim=768)
   ```
4. (Opcional) `OPENAI_API_KEY` para a transcrição via Whisper.
5. Token via [@BotFather](https://t.me/BotFather).

## Setup

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1            # Windows
# source .venv/bin/activate            # Linux/macOS

pip install -r requirements.txt
cp .env.example .env
# preencha TELEGRAM_BOT_TOKEN (e OPENAI_API_KEY se for usar voz)
```

> ⚠️ `EMBEDDING_DIM` precisa bater com o modelo de embedding escolhido.
> `nomic-embed-text` ⇒ 768; `mxbai-embed-large` ⇒ 1024. Se trocar, ajuste
> ANTES da primeira execução — caso contrário apague `data/faiss.index` e
> `data/faiss_id_map.json` para reindexar.

## Inicializar banco e índice

A primeira execução cria automaticamente:
- `data/bot.db` com **3 tabelas**:
  - `interactions` (campos abaixo)
  - `user_settings` (model + temperatura por usuário)
  - `pipeline_steps` (cada etapa de cada execução)
- `data/faiss.index` + `data/faiss_id_map.json`
- `data/media/` para mídias baixadas

DBs antigos são migrados automaticamente (ALTER TABLE ADD COLUMN para colunas
ausentes). Não há `manage.py migrate` — `SQLiteManager.init_schema()` faz tudo.

### Schema `interactions`

| coluna              | tipo    | observação                              |
|---------------------|---------|------------------------------------------|
| id                  | PK      |                                          |
| user_id, chat_id    | INTEGER |                                          |
| user_message        | TEXT    | input cru                                |
| bot_response        | TEXT    | resposta gerada                          |
| timestamp           | TEXT    | ISO-8601 UTC                             |
| media_path/type     | TEXT    | text / photo / voice / audio / document  |
| score               | INTEGER | NULL até o usuário avaliar               |
| tags                | JSON    | snake_case, livres                       |
| **intent**          | TEXT    | um de `ALLOWED_INTENTS`                  |
| **model_used**      | TEXT    | modelo Ollama efetivamente usado         |
| **temperature**     | REAL    |                                          |
| **prompt_tokens**   | INTEGER | `prompt_eval_count` do Ollama            |
| **response_tokens** | INTEGER | `eval_count`                             |
| **total_duration_ms**| INTEGER| `total_duration` (ns → ms)               |
| **prompt_used**     | TEXT    | prompt contrastivo final                 |
| **positive_ids**    | JSON    | IDs usados como bons exemplos            |
| **negative_ids**    | JSON    | IDs usados como ruins                    |
| **retrieved_count** | INTEGER | tamanho real do Top-K                    |
| **embedding_model** | TEXT    | qual modelo gerou o vetor                |
| **embedding_dim**   | INTEGER | dim do vetor salvo                       |
| **tool_calls**      | JSON    |                                          |
| **error**           | TEXT    | preenchido se a pipeline falhar          |
| **run_id**          | TEXT    | liga `interaction` a `pipeline_steps`    |

### Schema `pipeline_steps`

`run_id, step_index, step_name, status (ok|error|skipped), duration_ms,
details (JSON), error, timestamp` — uma linha por etapa, ligadas pelo `run_id`
ao `interactions.run_id`. Permite reconstruir a execução completa de qualquer
mensagem.

## Rodar o bot

```bash
python main.py
```

Você verá no terminal a pipeline detalhada de cada mensagem:

```
14:22:31.471 │ INFO    │ pipeline               │ [run=8ab3f1c2 u=12345] ▶ [01] load_user_settings        start  user_id=12345,media_type=text
14:22:31.479 │ INFO    │ pipeline               │ [run=8ab3f1c2 u=12345] ✓ [01] load_user_settings        ok        7ms  model=gemma:2b,temperature=0.7
14:22:31.480 │ INFO    │ pipeline               │ [run=8ab3f1c2 u=12345] ▶ [02] classify_intent           start  text_len=42
14:22:32.812 │ INFO    │ pipeline               │ [run=8ab3f1c2 u=12345] ✓ [02] classify_intent           ok     1331ms  intent=question,confidence=0.91
…
───── pipeline 8ab3f1c2 (user=12345, chat=12345) total≈3214ms ─────
  ✓ [01] load_user_settings              ok          7ms  model=gemma:2b,temperature=0.7
  ✓ [02] classify_intent                 ok       1331ms  intent=question,confidence=0.91
  ✓ [03] generate_tags                   ok        420ms  tags=[duvida,chat]
  ✓ [04] route_agent                     ok          0ms  route=chat,reason=default
  ✓ [05] rag_build                       ok        180ms  hits=12,positives=2,negatives=1,fallback_used=False
  ✓ [06] ollama_chat                     ok       1100ms  prompt_tokens=187,response_tokens=84
  ✓ [07] save_interaction                ok          5ms  interaction_id=58
  ✓ [08] index_interaction_embedding     ok        160ms  embed_ms=158,vec_dim=768
  ✓ [09] send_reply                      ok         11ms  reply_chars=312
──────────────────────────────────────────────────────────────────────
```

## Comandos do Telegram

| comando      | função                                                          |
|--------------|-----------------------------------------------------------------|
| `/start`     | saudação inicial                                                |
| `/help`      | lista de comandos                                               |
| `/config`    | escolher modelo (lista de `GET /api/tags`) e temperatura        |
| `/stats`     | total, avaliadas, positivas/negativas, latência média, FAISS    |
| `/recall <texto>` | mostra os hits do RAG: id, similaridade, score, bucket     |
| `/history [n]`    | últimas n interações suas (id, score, intent, modelo)      |
| `/ping`      | health-check do Ollama (modelos, dim live vs esperada)          |
| `/whoami`    | seu user_id, username e configuração ativa                      |
| `/reset`     | volta sua configuração ao padrão                                |

## Fluxo de uma mensagem (Pipeline)

1. **`load_user_settings`** — pega modelo + temperatura do usuário.
2. **`classify_intent`** — `IntentClassifier` retorna 1 de `ALLOWED_INTENTS`.
3. **`generate_tags`** — `TagGenerator` retorna 1..3 tags livres em snake_case.
4. **`route_agent`** — `AgentRouter` mapeia `tag/intent → AgentRoute`.
5. **`rag_build`** — Two-Stage:
   - Top-K em FAISS
   - metadados em SQLite, separa por score
   - **fallback neutro**: se 0 positivos e 0 negativos, usa Top-N como
     `[Contexto recente]` (assim o bot "puxa memória" mesmo sem ratings)
6. **`ollama_chat`** — `POST /api/chat` com tools registradas.
7. **`save_interaction`** — INSERT com prompt_used, tokens, latência,
   positive/negative IDs, embedding_dim, etc.
8. **`index_interaction_embedding`** — embed da interação `USER\n+\nBOT`
   adicionado ao FAISS.
9. **`send_reply`** — resposta com teclado `⭐ 1` … `⭐ 5`.
10. Clique do usuário ⇒ `callbacks.on_rate` faz `UPDATE interactions SET score`.

Cada etapa é cronometrada, logada e persistida em `pipeline_steps` (com o
mesmo `run_id` da interação).

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```

A suite cobre: `sqlite_mgr`, `faiss_mgr`, `prompt_templates`, `tag_generator`,
`intent_classifier`, `contrastive_rag` (com FakeOllama), `tools/registry`,
`agents/router`, `pipeline`. Não toca rede nem LLM real.

## Adicionar uma nova tool

```python
# tools/calculadora.py
from tools.registry import ToolRegistry, ToolSpec

async def _handler(expression: str) -> dict[str, str]:
    return {"result": str(eval(expression, {"__builtins__": {}}))}

def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="calc", description="Avalia uma expressão matemática.",
            parameters={
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
            handler=_handler,
        )
    )
```
E em `main.py`:
```python
from tools import calculadora
calculadora.register(registry)
```

## Como expandir o roteamento por agentes

`agents/router.py` mapeia `tag/intent → AgentRoute`. Para ativar fluxos
diferentes, instancie um `AgentRouter`, decida em
`handlers._process_user_input` antes do `rag.build(...)` e despache para
implementações específicas (CodeAgent, SearchAgent, etc.). A decisão já é
logada na etapa `route_agent` do pipeline.

## Notas de design

- **100% async** em I/O (Telegram, Ollama, Whisper, aiosqlite, FAISS via
  `asyncio.to_thread`).
- **Tipagem estrita** em todos os métodos públicos.
- **Injeção de dependência**: `BotDependencies` é montada em
  `main._bootstrap()` e injetada em `Application.bot_data`. Handlers leem
  via `_deps(context)`.
- **Observabilidade**: `PipelineRecorder` mede e loga toda etapa; persiste em
  `pipeline_steps` com `run_id` ligado à `interactions`.
- **Health-check no boot** valida `/api/tags` e mede a dim de embedding live
  contra `EMBEDDING_DIM` da config.
