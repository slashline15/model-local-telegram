# Chunking de Docs + Bot Debug + Consumo de Tokens

Três features relacionadas: o chunking resolve o gap de indexação, o consumo de tokens dá visibilidade real de custos, e o bot debug é o canal pra consultar tudo isso no celular.

---

## 1. Chunking de Documentos Grandes

### Problema
Hoje `_process_user_input` trunca o texto do documento em `_DOC_MAX_CHARS = 8000` chars e cria **1 único vetor** no FAISS. Documentos maiores perdem o miolo e a busca semântica não encontra nada que esteja depois dos primeiros 8k chars.

### Solução
Dividir o texto extraído em chunks de `_CHUNK_SIZE` chars (com overlap), e indexar **N vetores** no FAISS, todos apontando para a mesma `interaction_id`. O `faiss_id_map` já guarda `{faiss_internal_id → interaction_id}` — múltiplos IDs do FAISS podem apontar pro mesmo `interaction_id`.

### Arquivos afetados

#### [MODIFY] [handlers.py](file:///c:/projects/lham/model-local-telegram/tg/handlers.py)

1. **`on_document`** (linha ~722): Remover truncagem em `_DOC_MAX_CHARS`. Manter texto completo para o prompt (ainda truncado no prompt do LLM, mas indexar tudo).

2. **`_process_user_input` → step `index_interaction_embedding`** (linha ~999): Substituir a indexação de um único vetor por loop que:
   - Divide `raw_embed_text` em chunks de `_CHUNK_SIZE = 2500` chars com overlap de `_CHUNK_OVERLAP = 300` chars
   - Gera embedding de cada chunk
   - Insere cada vetor no FAISS apontando pra `interaction_id`

3. Nova função **`_chunk_text(text, chunk_size, overlap)`** — função pura que retorna `list[str]`.

#### [MODIFY] [config.py](file:///c:/projects/lham/model-local-telegram/core/config.py)
- Adicionar `chunk_size: int = 2500` e `chunk_overlap: int = 300`

#### [MODIFY] [faiss_mgr.py](file:///c:/projects/lham/model-local-telegram/database/faiss_mgr.py)
- Verificar se `add()` já aceita múltiplas chamadas com o mesmo `interaction_id` (provavelmente sim — o mapa é `faiss_id → interaction_id`, não o contrário). Se não, adaptar.

> [!NOTE]
> Não precisa de migration no SQLite. Os chunks são transparentes para o banco — só o FAISS sabe que existem N vetores pra 1 interaction.

---

## 2. Tabela `token_usage` — Consumo de Tokens

### Problema
Os tokens já são salvos em `interactions.prompt_tokens` e `interactions.response_tokens`, mas:
- Não separam consumo de **classificação** (intent + tags) do consumo de **chat**
- Não rastreiam consumo de **embedding**
- Não têm visão agregada fácil (por modelo, por obra, por período)

### Solução
Nova tabela `token_usage` com granularidade por **operação** dentro de cada pipeline run.

### Arquivos afetados

#### [MODIFY] [schema.py](file:///c:/projects/lham/model-local-telegram/database/schema.py)
```sql
CREATE TABLE IF NOT EXISTS token_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT    NOT NULL,
    interaction_id  INTEGER REFERENCES interactions(id),
    user_id         INTEGER NOT NULL,
    project_id      INTEGER,
    model           TEXT    NOT NULL,
    backend         TEXT    NOT NULL DEFAULT 'ollama',  -- ollama | openai
    operation       TEXT    NOT NULL,
    -- classify_intent | generate_tags | chat | chat_fallback |
    -- embedding | whisper_transcription
    prompt_tokens   INTEGER NOT NULL DEFAULT 0,
    response_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL
);
```
Índices: `idx_token_usage_user`, `idx_token_usage_project`, `idx_token_usage_model`, `idx_token_usage_date` (sobre `created_at`).

#### [NEW] [database/repos/token_usage.py](file:///c:/projects/lham/model-local-telegram/database/repos/token_usage.py)
- `insert(run_id, interaction_id, user_id, project_id, model, backend, operation, prompt_tokens, response_tokens, duration_ms)`
- `sum_by_user(user_id, since=None)` → dict com totais por modelo
- `sum_by_project(project_id, since=None)` → dict com totais por modelo
- `sum_by_model(since=None)` → dict com totais por modelo
- `daily_breakdown(since, user_id=None, project_id=None)` → lista dia-a-dia

#### [MODIFY] [models.py](file:///c:/projects/lham/model-local-telegram/database/models.py)
- `TokenUsageRow` (dataclass)
- `TokenUsageSummary` (dataclass para os aggregates)

#### [MODIFY] [sqlite_mgr.py](file:///c:/projects/lham/model-local-telegram/database/sqlite_mgr.py)
- Instanciar `TokenUsageRepo`, expor `self.token_usage`

#### [MODIFY] [handlers.py](file:///c:/projects/lham/model-local-telegram/tg/handlers.py)
- Após cada chamada LLM (classify, tags, chat, embedding), chamar `deps.sqlite.token_usage.insert(...)` com os dados do `ChatResult` ou contagem de embedding.

> [!IMPORTANT]
> O embedding do Ollama (`/api/embeddings`) **não retorna contagem de tokens**. Vamos estimar com `len(text) // 4` (heurística chars → tokens) e gravar `prompt_tokens=estimado, response_tokens=0`. O campo `duration_ms` dá a métrica real de custo.

---

## 3. Bot de Debug (Telegram)

### Conceito
Segundo bot Telegram (token separado: `TELEGRAM_DEBUG_BOT_TOKEN`), que:
- **Recebe logs importantes automaticamente** — resumo mobile-friendly de cada pipeline run (tokens, modelo, tempo, usuário)
- **Tem comandos de consulta** — consumo por usuário, por obra, por modelo
- Envia apenas para `BOOTSTRAP_SUPERADMIN_TELEGRAM_ID`

### Arquivos afetados

#### [MODIFY] [config.py](file:///c:/projects/lham/model-local-telegram/core/config.py)
```python
telegram_debug_bot_token: str = Field(default="", description="Token do bot de debug")
debug_mode: bool = Field(default=False, description="Se True, ativa o bot de debug")
```

#### [MODIFY] [.env](file:///c:/projects/lham/model-local-telegram/.env)
```env
# --- Debug Bot ---
TELEGRAM_DEBUG_BOT_TOKEN=""
DEBUG_MODE=false
```

#### [NEW] [tg/debug_bot.py](file:///c:/projects/lham/model-local-telegram/tg/debug_bot.py)
Módulo com:

**`DebugNotifier`** — classe que envia mensagens para o superadmin via bot de debug:
- `notify_pipeline_run(run_id, user_name, model, tokens, duration_ms, intent, tags, backend, project_name)` — chamado no final de `_process_user_input`
- `notify_error(run_id, error_text)` — chamado em caso de falha
- Formato mobile-friendly:
  ```
  📊 Pipeline · d22f673f
  👤 Daniel · 🏗 Reforma Igreja
  🤖 gemma4:31b-cloud (ollama)
  💬 intent=question · tags=duvida,codigo
  📥 342 prompt + 128 resp = 470 tokens
  ⏱ 2.4s total
  ```

**Comandos do bot debug** (handlers separados, registrados só nesse bot):
- `/consumo` ou `/uso` — resumo geral (hoje, semana, mês)
- `/consumo_usuario <tg_id ou nome>` — detalhamento por usuário
- `/consumo_obra <uid>` — detalhamento por obra
- `/consumo_modelo` — ranking de modelos por tokens gastos
- `/status` — health do sistema (Ollama, FAISS, DB size)

#### [MODIFY] [bot.py](file:///c:/projects/lham/model-local-telegram/tg/bot.py)
- `BotDependencies` ganha campo `debug_notifier: DebugNotifier | None`

#### [MODIFY] [main.py](file:///c:/projects/lham/model-local-telegram/main.py)
- Se `debug_mode=True` e `telegram_debug_bot_token` presente:
  - Instancia `DebugNotifier`
  - Cria segundo `Application` com o token do debug
  - Registra handlers de consulta nesse app
  - Roda ambos os bots em paralelo (o PTB suporta via `asyncio.gather` no loop principal, ou rodando o debug bot separadamente com `run_polling` usando `stop_signals=[]` para não conflitar)

#### [MODIFY] [handlers.py](file:///c:/projects/lham/model-local-telegram/tg/handlers.py)
- No `finally` de `_process_user_input`, após salvar pipeline_steps, chamar `debug_notifier.notify_pipeline_run(...)` se disponível

> [!WARNING]
> O PTB **não suporta** dois `app.run_polling()` no mesmo processo facilmente (ambos competem por signals). A solução é: o bot debug **não usa `run_polling()`** do PTB. Usamos diretamente a API do Telegram (`getUpdates` via aiohttp) ou, mais simples, usamos o bot debug **apenas para enviar** (sem polling). Os comandos de consulta ficam no bot principal, acessíveis apenas ao superadmin via middleware.

### Design revisado — Bot debug simplificado

Em vez de um segundo bot com polling próprio, o design mais limpo é:

1. **`DebugNotifier`** usa o token do debug bot **apenas para enviar** mensagens ao superadmin (API `sendMessage` direta, sem PTB)
2. **Comandos de consulta** (`/consumo`, `/consumo_usuario`, etc.) ficam no **bot principal**, protegidos por `@require_superadmin`
3. Isso elimina a complexidade de dois pollers

---

## Open Questions

> [!IMPORTANT]
> **Nome da variável de ambiente**: No .env você escreveu `TELEGRAM_API_TELEGRAM_DEBUG`. Prefere esse nome exato ou posso usar `TELEGRAM_DEBUG_BOT_TOKEN` (mais consistente com `TELEGRAM_BOT_TOKEN`)?

> [!IMPORTANT]
> **Transcrição Whisper**: quer rastrear o consumo do Whisper também na tabela `token_usage`? O Whisper cobra por duração do áudio (não por tokens), mas posso gravar `duration_ms` do áudio + tempo da API.

> [!NOTE]
> **Chunking no reindex**: o script `scripts/reindex` vai precisar de ajuste pra gerar múltiplos vetores por interação. Incluo isso no escopo?

---

## Verificação

### Testes automatizados
- `test_chunking.py` — testa `_chunk_text()` com textos de vários tamanhos, verifica overlap, edge cases
- `test_token_usage.py` — CRUD do repo, queries agregadas por user/project/model
- Testes existentes devem continuar passando (nenhuma quebra de API)

### Testes manuais
- Enviar PDF grande (>8k chars) e verificar que `/recall` encontra trechos do final do documento
- Ver log do bot debug no celular com resumo da pipeline
- `/consumo` mostra totais corretos
