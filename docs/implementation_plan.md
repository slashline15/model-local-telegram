# Chunking de Docs + Bot Debug + Consumo de Tokens (rev. 2)

Fontes de verdade: [Hierarquia de documentos.md](file:///c:/projects/lham/model-local-telegram/docs/Hierarquia%20de%20documentos.md) · [Chunking + Tokens + Bot Debug.md](file:///c:/projects/lham/model-local-telegram/docs/Chunking%20+%20Tokens%20+%20Bot%20Debug.md) · [Níveis de acesso.md](file:///c:/projects/lham/model-local-telegram/docs/N%C3%ADveis%20de%20acesso.md)

---

## 1. Chunking de Documentos Grandes

### Problema confirmado
O `FaissManager` usa `IndexIDMap2.add_with_ids(v, [sqlite_id])` — o **FAISS ID é o sqlite_id**. Inserir o mesmo `interaction_id` duas vezes **sobrescreve**. Precisa de uma tabela intermediária.

### Schema: `interaction_chunks`

```sql
CREATE TABLE IF NOT EXISTS interaction_chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    interaction_id  INTEGER NOT NULL REFERENCES interactions(id),
    chunk_idx       INTEGER NOT NULL,
    content         TEXT    NOT NULL,
    doc_class       TEXT    NOT NULL DEFAULT 'note',
    -- contract | spec | norm | proposal | note | meeting | other
    weight          REAL    NOT NULL DEFAULT 1.0,
    -- Pré-calculado: class_weight * sender_boost (nunca recalcular no retrieval)
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_interaction ON interaction_chunks(interaction_id);
```

### Pesos por classe (da [Hierarquia de documentos.md](file:///c:/projects/lham/model-local-telegram/docs/Hierarquia%20de%20documentos.md))

| Classe | Peso |
|--------|------|
| `contract` | 1.5 |
| `spec` | 1.4 |
| `norm` | 1.3 |
| `proposal` | 1.1 |
| `note` | 1.0 |
| `other` | 0.8 |
| `meeting` | 0.7 |

### Boost por papel do remetente (da [Níveis de acesso.md](file:///c:/projects/lham/model-local-telegram/docs/N%C3%ADveis%20de%20acesso.md))

| Nível | Boost |
|-------|-------|
| N1 (admin) | +0.2 |
| N2 (co-responsável) | +0.1 |
| N3 (operacional) | 0 |

### Coleta da classe
Botões inline no upload + parse de caption (`#contrato`, `#memorial`, `#reuniao`). **Sem auto-classificação por LLM nesta fase.**

### Fórmula de retrieval
```python
score_final = score_similarity * chunk.weight
# weight foi pré-calculado na inserção = class_weight * (1.0 + sender_boost)
# Aplicar ANTES do top-k cut
```

### Arquivos afetados

---

#### [MODIFY] [schema.py](file:///c:/projects/lham/model-local-telegram/database/schema.py)
- Adicionar `_INTERACTION_CHUNKS_BASE` DDL
- Adicionar à tupla `_TABLES`
- Adicionar índice `idx_chunks_interaction`

#### [NEW] [database/repos/chunks.py](file:///c:/projects/lham/model-local-telegram/database/repos/chunks.py)
- `ChunksRepo(BaseRepo)`:
  - `insert(interaction_id, chunk_idx, content, doc_class, weight) → int` (retorna chunk_id)
  - `insert_many(interaction_id, chunks: list[ChunkInsert]) → list[int]` (bulk, retorna chunk_ids)
  - `get_by_interaction(interaction_id) → list[InteractionChunk]`
  - `get_by_ids(chunk_ids) → list[InteractionChunk]`
  - `get_interaction_ids_for_chunks(chunk_ids) → dict[int, int]` (chunk_id → interaction_id, para o retrieval)

#### [MODIFY] [models.py](file:///c:/projects/lham/model-local-telegram/database/models.py)
```python
@dataclass(slots=True, frozen=True)
class InteractionChunk:
    id: int
    interaction_id: int
    chunk_idx: int
    content: str
    doc_class: str
    weight: float
    created_at: str
```

#### [MODIFY] [sqlite_mgr.py](file:///c:/projects/lham/model-local-telegram/database/sqlite_mgr.py)
- Instanciar `ChunksRepo`, expor `self.chunks`

#### [MODIFY] [repos/__init__.py](file:///c:/projects/lham/model-local-telegram/database/repos/__init__.py)
- Adicionar `ChunksRepo` aos exports

#### [MODIFY] [faiss_mgr.py](file:///c:/projects/lham/model-local-telegram/database/faiss_mgr.py)
**Refatoração:** FAISS ID passa a ser `chunk_id` (não mais `interaction_id`).
- `add(chunk_id, vector)` — renomear parâmetro de `sqlite_id` para `chunk_id`
- `_known_ids` passa a ser set de chunk_ids
- `search()` retorna `list[tuple[int, float]]` onde int = chunk_id (não mais interaction_id)
- Adicionar `add_many(chunk_ids, vectors)` para bulk insert

#### [MODIFY] [contrastive_rag.py](file:///c:/projects/lham/model-local-telegram/llm/contrastive_rag.py)
**Refatoração do retrieval:**
1. `search()` retorna chunk_ids + similarities
2. JOIN com `interaction_chunks` via `chunks.get_by_ids(chunk_ids)` para obter `weight` e `interaction_id`
3. Aplicar `score_final = similarity * weight` **antes** do top-k cut
4. Deduplicar por `interaction_id` (manter o chunk com maior `score_final`)
5. Depois carregar `Interaction` rows com `fetch_by_ids()` como hoje

Precisa receber `chunks_repo` no construtor.

#### [MODIFY] [handlers.py](file:///c:/projects/lham/model-local-telegram/tg/handlers.py)
**`on_document`** — após upload:
1. Parse caption para `doc_class`: `#contrato` → `contract`, `#memorial` → `spec`, etc.
2. Se não tem hashtag, mostrar botões inline para o usuário escolher a classe (com fallback `note` se ignorar — timeout 30s)
3. Remover truncagem em `_DOC_MAX_CHARS` para indexação (manter para o prompt LLM)

**`_process_user_input` → step `index_interaction_embedding`:**
1. Chamar `_chunk_text(raw_embed_text, chunk_size, overlap)` → `list[str]`
2. Determinar `weight = CLASS_WEIGHTS[doc_class] * (1.0 + sender_boost)`
3. Para cada chunk: embed → inserir em `interaction_chunks` → adicionar chunk_id no FAISS
4. Usar `deps.sqlite.chunks.insert_many()` + `deps.faiss.add_many()`

**Nova função `_chunk_text(text, chunk_size, overlap) → list[str]`**

**Mapa de hashtags:**
```python
_CAPTION_CLASS_MAP = {
    "#contrato": "contract", "#aditivo": "contract",
    "#memorial": "spec", "#projeto": "spec", "#especificacao": "spec",
    "#norma": "norm", "#nr": "norm",
    "#proposta": "proposal", "#escopo": "proposal",
    "#nota": "note", "#anotacao": "note",
    "#reuniao": "meeting", "#ata": "meeting",
}
```

#### [MODIFY] [config.py](file:///c:/projects/lham/model-local-telegram/core/config.py)
```python
chunk_size: int = Field(default=2500)
chunk_overlap: int = Field(default=300)
```

#### [MODIFY] [scripts/reindex.py](file:///c:/projects/lham/model-local-telegram/scripts/reindex.py)
- Adaptar para gerar chunks + usar chunk_ids no FAISS
- Ler `interaction_chunks` existentes se houver; se não, criar 1 chunk por interaction (migração)

---

## 2. Consumo de Tokens + Pricing

### Tabelas

#### `token_usage`
```sql
CREATE TABLE IF NOT EXISTS token_usage (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT    NOT NULL,
    interaction_id      INTEGER REFERENCES interactions(id),
    user_id             INTEGER NOT NULL,
    project_id          INTEGER,
    model               TEXT    NOT NULL,
    backend             TEXT    NOT NULL DEFAULT 'ollama',
    operation           TEXT    NOT NULL,
    -- classify_intent | generate_tags | chat | chat_fallback |
    -- embedding | whisper_transcription
    prompt_tokens       INTEGER NOT NULL DEFAULT 0,
    response_tokens     INTEGER NOT NULL DEFAULT 0,
    total_tokens        INTEGER NOT NULL DEFAULT 0,
    duration_ms         INTEGER NOT NULL DEFAULT 0,
    quantity_secondary  REAL    DEFAULT 0,
    -- Para Whisper: duração do áudio em segundos
    -- Para embedding: número de chunks processados
    created_at          TEXT    NOT NULL
);
```

#### `model_pricing`
```sql
CREATE TABLE IF NOT EXISTS model_pricing (
    model              TEXT    PRIMARY KEY,
    backend            TEXT    NOT NULL,
    cost_per_1k_input  REAL    DEFAULT 0,
    cost_per_1k_output REAL    DEFAULT 0,
    currency           TEXT    DEFAULT 'USD',
    updated_at         TEXT    NOT NULL
);
```

Seed inicial:
```python
_MODEL_PRICING_SEED = [
    ("gemma4:31b-cloud", "ollama", 0, 0, "USD"),
    ("llama3.2:3b", "ollama", 0, 0, "USD"),
    ("nomic-embed-text:v1.5", "ollama", 0, 0, "USD"),
    ("gpt-4o-mini", "openai", 0.00015, 0.0006, "USD"),
    ("whisper-1", "openai", 0.006, 0, "USD"),  # $0.006/min
]
```

### Arquivos afetados

#### [MODIFY] [schema.py](file:///c:/projects/lham/model-local-telegram/database/schema.py)
- `_TOKEN_USAGE_BASE`, `_MODEL_PRICING_BASE` DDLs
- Adicionar à tupla `_TABLES`
- Índices: `idx_token_usage_user`, `idx_token_usage_project`, `idx_token_usage_model`, `idx_token_usage_created_at`
- Seed de `model_pricing` (como `_seed_model_pricing()`, idempotente)

#### [NEW] [database/repos/token_usage.py](file:///c:/projects/lham/model-local-telegram/database/repos/token_usage.py)
`TokenUsageRepo(BaseRepo)`:
- `insert(...)` — grava uma linha
- `sum_by_user(user_id, since=None) → list[TokenUsageSummary]` — agrupado por modelo
- `sum_by_project(project_id, since=None) → list[TokenUsageSummary]`
- `sum_by_model(since=None) → list[TokenUsageSummary]`
- `daily_breakdown(days=7, user_id=None, project_id=None) → list[DailyTokenRow]` — para sparkline
- `top_users(since, limit=10) → list[tuple[int, str, int, float]]` — (user_id, name, total_tokens, cost_usd)
- `top_projects(since, limit=10) → list[tuple[int, str, int, float]]`

#### [NEW] [database/repos/model_pricing.py](file:///c:/projects/lham/model-local-telegram/database/repos/model_pricing.py)
`ModelPricingRepo(BaseRepo)`:
- `get(model) → ModelPricing | None`
- `get_all() → list[ModelPricing]`
- `upsert(model, backend, cost_per_1k_input, cost_per_1k_output, currency)`
- `calc_cost(model, prompt_tokens, response_tokens) → float` — calcula custo em USD

#### [MODIFY] [models.py](file:///c:/projects/lham/model-local-telegram/database/models.py)
```python
@dataclass(slots=True, frozen=True)
class TokenUsageRow:
    id: int
    run_id: str
    interaction_id: int | None
    user_id: int
    project_id: int | None
    model: str
    backend: str
    operation: str
    prompt_tokens: int
    response_tokens: int
    total_tokens: int
    duration_ms: int
    quantity_secondary: float
    created_at: str

@dataclass(slots=True, frozen=True)
class TokenUsageSummary:
    model: str
    backend: str
    total_prompt: int
    total_response: int
    total_tokens: int
    total_duration_ms: int
    cost_usd: float
    count: int

@dataclass(slots=True, frozen=True)
class DailyTokenRow:
    date: str
    total_tokens: int
    cost_usd: float

@dataclass(slots=True, frozen=True)
class ModelPricing:
    model: str
    backend: str
    cost_per_1k_input: float
    cost_per_1k_output: float
    currency: str
    updated_at: str
```

#### [MODIFY] [sqlite_mgr.py](file:///c:/projects/lham/model-local-telegram/database/sqlite_mgr.py)
- Instanciar `TokenUsageRepo` e `ModelPricingRepo`

#### [MODIFY] [repos/__init__.py](file:///c:/projects/lham/model-local-telegram/database/repos/__init__.py)
- Adicionar `TokenUsageRepo`, `ModelPricingRepo` aos exports

#### [MODIFY] [handlers.py](file:///c:/projects/lham/model-local-telegram/tg/handlers.py)
Após cada chamada LLM registrar em `token_usage`:

| Operação | Onde | Tokens |
|----------|------|--------|
| `classify_intent` | step `classify_intent` | `ChatResult.prompt_tokens/response_tokens` |
| `generate_tags` | step `generate_tags` | `ChatResult.prompt_tokens/response_tokens` |
| `chat` | step `ollama_chat` | `ChatResult.prompt_tokens/response_tokens` |
| `embedding` | step `index_interaction_embedding` | `prompt_tokens=len(text)//4, response_tokens=0` |
| `whisper_transcription` | `on_voice` | `prompt_tokens=0, response_tokens=0, quantity_secondary=audio_seconds` |

> [!NOTE]
> Intent classifier e tag generator não retornam tokens hoje — seus `ChatResult` têm `prompt_tokens` e `response_tokens`. Preciso confirmar que o OllamaClient popula esses campos. Se sim, está pronto. Se não, estimo com `len(text)//4`.

---

## 3. Bot de Debug

### Design
- **`DebugNotifier`** usa o token `TELEGRAM_DEBUG_BOT_TOKEN` **apenas para enviar** (API direta, sem PTB, sem polling)
- **Comandos de consulta** ficam no **bot principal**, protegidos por `@require_superadmin` (novo decorator)
- Filtros configuráveis em `.env` para evitar spam

### Filtros de notificação

```env
# --- Debug Bot ---
TELEGRAM_DEBUG_BOT_TOKEN=""
DEBUG_MODE=false
DEBUG_NOTIFY_MIN_COST_USD=0.001
DEBUG_NOTIFY_SAMPLE_RATE=0.05
DEBUG_NOTIFY_ON_ERROR=true
DEBUG_NOTIFY_ON_LATENCY_MS=10000
```

Lógica: notificar se **qualquer** condição for true:
1. `cost_usd >= min_cost`
2. `random() < sample_rate` (amostragem aleatória)
3. `on_error=true` e pipeline teve erro
4. `duration_ms >= on_latency_ms`

### Formato mobile-friendly
```
📊 Pipeline · d22f673f
👤 Daniel · 🏗 Reforma Igreja
🤖 gemma4:31b-cloud (ollama)
💬 intent=question · tags=duvida,codigo
📥 342 in + 128 out = 470 tok · $0.00
⏱ 2.4s total
```

Se erro:
```
🔴 Pipeline ERRO · d22f673f
👤 Daniel · 🏗 Reforma Igreja
❌ OllamaTimeoutError: Connection refused
⏱ 10.2s
```

### Arquivos afetados

#### [MODIFY] [config.py](file:///c:/projects/lham/model-local-telegram/core/config.py)
```python
telegram_debug_bot_token: str = Field(default="")
debug_mode: bool = Field(default=False)
debug_notify_min_cost_usd: float = Field(default=0.001)
debug_notify_sample_rate: float = Field(default=0.05)
debug_notify_on_error: bool = Field(default=True)
debug_notify_on_latency_ms: int = Field(default=10000)
```

#### [NEW] [tg/debug_notifier.py](file:///c:/projects/lham/model-local-telegram/tg/debug_notifier.py)
`DebugNotifier`:
- Usa `aiohttp` direto para `POST https://api.telegram.org/bot{token}/sendMessage`
- `async def notify_pipeline_run(...)` — formata e envia se filtros passarem
- `async def notify_error(...)` — sempre envia (se `on_error=true`)
- `async def close()` — fecha aiohttp session

#### [MODIFY] [middleware.py](file:///c:/projects/lham/model-local-telegram/tg/middleware.py)
Novo decorator:
```python
def require_superadmin(handler: Handler) -> Handler:
    @require_active_user
    @wraps(handler)
    async def wrapper(update, context):
        user = get_bot_user(context)
        if user.role != "superadmin":
            await update.effective_message.reply_text("⛔ Só superadmin.")
            return None
        return await handler(update, context)
    return wrapper
```

#### [NEW] [tg/handlers_debug.py](file:///c:/projects/lham/model-local-telegram/tg/handlers_debug.py)
Comandos no bot principal, protegidos por `@require_superadmin`:

**`/consumo`** — resumo geral:
```
📊 Consumo de tokens

Hoje:     12.4k tokens · $0.02
Ontem:     8.1k tokens · $0.01
Semana:   45.2k tokens · $0.07
Mês:     180.5k tokens · $0.28

Últimos 7 dias:
▂▃▅▇█▅▃

Por modelo (semana):
  gemma4:31b-cloud  38.2k  $0.00
  gpt-4o-mini        5.1k  $0.04
  whisper-1          2.1k  $0.03
```

**`/consumo_usuario <id ou nome>`** — detalhamento por user
**`/consumo_obra <uid>`** — detalhamento por obra
**`/consumo_modelo`** — ranking de modelos com custo $

**`/status`** — health do sistema:
```
🟢 Status do sistema

🤖 Ollama: online · última inferência 2.4s
📊 FAISS: 1,234 vetores · 4.8 MB
💾 DB: 12.3 MB · +0.8 MB (24h)
⚠️ Última falha: OllamaTimeout (há 3h)
⏱ Uptime: 4d 12h 33m
```

#### [MODIFY] [bot.py](file:///c:/projects/lham/model-local-telegram/tg/bot.py)
- `BotDependencies` ganha `debug_notifier: DebugNotifier | None`
- Registrar handlers de `/consumo*`, `/status` no `build_application()`
- Adicionar BotCommands para os novos comandos (só aparecem pro superadmin)

#### [MODIFY] [main.py](file:///c:/projects/lham/model-local-telegram/main.py)
- Se `debug_mode=True` e `telegram_debug_bot_token` presente: instanciar `DebugNotifier`
- Injetar em `BotDependencies`
- Fechar no shutdown

#### [MODIFY] [handlers.py](file:///c:/projects/lham/model-local-telegram/tg/handlers.py)
No `finally` de `_process_user_input`:
1. Calcular `cost_usd` via `model_pricing.calc_cost()`
2. Checar filtros de notificação
3. Se algum filtro match: `await debug_notifier.notify_pipeline_run(...)`
4. Se erro: `await debug_notifier.notify_error(...)`

#### [MODIFY] [.env](file:///c:/projects/lham/model-local-telegram/.env)
Adicionar as variáveis do debug bot.

---

## Ordem de implementação

1. **Schema + models** — tabelas novas, dataclasses, migrations
2. **Repos** — `ChunksRepo`, `TokenUsageRepo`, `ModelPricingRepo`
3. **FaissManager refactor** — chunk_id em vez de interaction_id
4. **ContrastiveRAG refactor** — retrieval com chunks + weight
5. **Chunking na pipeline** — `_chunk_text()`, indexação multi-chunk
6. **Token tracking** — instrumentação nas etapas da pipeline
7. **Middleware `require_superadmin`**
8. **DebugNotifier** — envio de notificações
9. **Handlers debug** — `/consumo`, `/status`
10. **Reindex script** — adaptação para chunks
11. **Testes**

---

## Verificação

### Testes automatizados
- `test_chunking.py` — `_chunk_text()` com textos de vários tamanhos, overlap, edge cases (vazio, menor que chunk_size, exatamente chunk_size)
- `test_chunks_repo.py` — CRUD de `interaction_chunks`, `get_interaction_ids_for_chunks`
- `test_token_usage.py` — insert, `sum_by_user/project/model`, `daily_breakdown`
- `test_model_pricing.py` — upsert, `calc_cost`
- `test_faiss_refactor.py` — add com chunk_id, search retorna chunk_ids
- `test_rag_weighted.py` — retrieval aplica weight antes do top-k
- Testes existentes: adaptar os que usam `faiss.add(interaction_id, vec)` para usar chunk_id

### Testes manuais
- Enviar PDF grande com `#contrato` na caption → verificar chunks no DB e recall ponderado
- `/consumo` mostra sparkline e custos corretos
- `/status` mostra health real
- Notificação chega no bot debug quando custo > threshold
