# Notas de sessão — para retomar contexto rápido

Resumo das decisões não-óbvias e armadilhas que custaram tempo. Se você é
um agente entrando frio neste projeto, leia isto antes de mexer.

## Arquitetura em 30 segundos

`tg/` (Telegram) → `tg/handlers._process_user_input` (pipeline) →
`llm/contrastive_rag.build()` monta system+user prompt →
`llm/ollama_client.chat()` POST /api/chat → tool loop → resposta →
sanitização Markdown → reply com teclado de rating →
`callbacks.on_rate` faz UPDATE score.

Toda etapa cronometrada por `core/pipeline.PipelineRecorder`, persistida em
`pipeline_steps` ligada por `run_id` à `interactions`.

## Armadilhas resolvidas (não recriar)

### 1. Event loop closed entre bootstrap e python-telegram-bot
`asyncio.run(_bootstrap())` cria loop A; `app.run_polling()` cria loop B.
Se `aiohttp.ClientSession` ou `asyncio.Lock` forem instanciados em A, eles
quebram em B. **Solução**: `OllamaClient` cria session+lock+loop_ref de
forma lazy em `_get_session()`, e detecta troca de loop comparando com
`asyncio.get_running_loop()`. Health-check **precisa** rodar em
`tg/bot._on_post_init`, NÃO em `main._bootstrap`. Teste de regressão:
`tests/test_ollama_session_reuse.py`.

### 2. SQLite "no such column: intent" em DBs legados
`init_schema()` original criava índices ANTES de aplicar migrations →
`CREATE INDEX idx_interactions_intent` rodava antes do `ALTER TABLE ADD
COLUMN intent`. **Solução**: ordem é tabelas → migrations → índices.
Teste: `tests/test_sqlite_mgr.py::test_migrates_legacy_db`.

### 3. RAG perdia contexto em mensagens curtas
Top-K semântico em "O nome correto é betoneiro" pega vizinhos errados.
**Solução**: `RAG.build` busca os últimos N turnos cronológicos do
`user_id` via `sqlite.list_user_history` e injeta como bloco
`[Histórico recente]` antes do contrastivo. Configurável:
`RAG_RECENT_HISTORY=6`. Dedupa contra hits do FAISS para não duplicar.

### 4. `summarize` sumarizava as próprias âncoras do prompt
Modelo via "as informações apresentadas" e descrevia
`[O QUE FAZER]/[O QUE NÃO FAZER]`. **Solução**: para `intent="summarize"`,
`RAG.build` bypassa contrastivo e usa `render_qa_prompt` (só histórico +
pergunta).

### 5. Embedding 500: "input length exceeds the context length"
PDF grande + resposta = `embed_text` com 8k+ chars. `nomic-embed-text`
tem limite de ~8k tokens. **Solução**: truncar `embed_text` em 3000 chars
(`_EMBED_INPUT_MAX_CHARS` em `tg/handlers.py`). `classify_intent` e
`generate_tags` também recebem só 2k chars (`_CLASSIFY_INPUT_MAX_CHARS`).
A chat principal continua recebendo o input completo.

### 6. Tool calling não fechava o loop
Modelo retornava `tool_calls`, salvávamos no DB mas nunca executávamos
nem realimentávamos. **Solução**: loop até 3 iterações em
`_process_user_input::ollama_chat` step: `dispatch` → `role=tool`
mensagem → re-chamar `chat`. Ecoa o turno `assistant` com `tool_calls`
para o modelo manter contexto.

### 7. Imagem ignorada por modelo sem visão
Bot dizia "nenhuma imagem foi fornecida" mesmo enviando `images_b64`.
Causa: modelo não suportava visão. **Solução**: `_VISION_PATTERNS` em
`tg/handlers.py` — checa substring no nome do modelo e avisa o usuário
antes de processar. Não bloqueia, só alerta.

### 8. `BadRequest: Message is not modified`
Usuário clicava no mesmo botão de rating duas vezes →
`edit_message_reply_markup` explodia. **Solução**:
`_safe_clear_keyboard` em `tg/callbacks.py` engole esse erro específico.

### 9. Bot não sabia a data
Respondeu "22 de maio de 2024" num turno de 2026. **Solução**:
`build_system_prompt(now_iso)` em `prompt_templates.py` injeta data/hora
local. `_now_local_iso()` em handlers.py é a fonte.

### 10. LaTeX cru no Telegram (`$\rightarrow$`)
Modelo Ollama vomita LaTeX para setas. **Solução em duas camadas**:
- Diretiva no system prompt proibindo LaTeX e listando substitutos.
- `_sanitize_for_telegram(text)` em handlers como rede de segurança:
  substitui `\rightarrow` → `→` etc., e colapsa `$...$` / `\(...\)` /
  `\[...\]` para o conteúdo plain.

### 11. Markdown não renderizado
`reply_text` sem `parse_mode`. **Solução**: `_safe_reply` tenta
`parse_mode=Markdown` (legacy), e em caso de `BadRequest` com mensagem de
parse/entity, retry sem parse_mode. Não usamos MarkdownV2 (escape
estrito).

## Conhecimento operacional

### Reindex
```bash
python -m scripts.reindex --dry-run    # auditoria
python -m scripts.reindex              # apaga FAISS, regenera tudo
```
SQLite intocado. Use após trocar `EMBEDDING_MODEL`/`EMBEDDING_DIM` ou se
suspeitar de entradas órfãs no FAISS.

### Fluxo de teste recomendado
```bash
pytest -x --tb=short      # 39 testes, ~1s
python -c "import tg.handlers, scripts.reindex; print('OK')"
python main.py            # teste manual; observe a pipeline no terminal
```

### Modelos com visão (substring detectada)
`llava`, `vision`, `-vl`, `moondream`, `bakllava`, `minicpm-v`,
`qwen2.5-vl`, `qwen2-vl`, `gemma3`, `gemma4`, `llama3.2-vision`,
`pixtral`, `phi3.5-vision`. Ajuste `_VISION_PATTERNS` ao adotar novos.

### Limites cartesianos
- `_DOC_MAX_CHARS = 8000` — quanto de PDF/texto entra na pipeline.
- `_EMBED_INPUT_MAX_CHARS = 3000` — quanto vai para o embedder.
- `_CLASSIFY_INPUT_MAX_CHARS = 2000` — para intent/tag classifiers.
- `_TOOL_LOOP_MAX_ITER = 3` — quantas rodadas de tool calling.
- `RAG_RECENT_HISTORY = 6` — turnos cronológicos no prompt.

## Pendente (próximas sessões)

1. **Preferências persistentes** — `style_directive` em `user_settings`
   (existe um param em `build_system_prompt` esperando), comando
   `/style "responda em até 5 linhas"`. Tabela `user_facts` para fatos
   tipo "betoneiro, não betoneira" + comando `/remember`.
2. **`web_search` real** — substituir mock em `tools/web_search.py`. A
   diretiva no system prompt já manda citar fontes, então basta o tool
   retornar `{"results": [{"title", "url", "snippet"}]}`.
3. **Chunking de documento** — hoje indexamos 1 vetor por interação. PDF
   longo perde o miolo. Para indexar N chunks com mesma `interaction_id`
   ou IDs derivados, vide `_process_user_input::index_interaction_embedding`.
4. **Comando `/reindex` via Telegram** — hoje é só script. Útil pra
   admin remoto. Usar `bot_data["deps"].sqlite/faiss/ollama` direto.

## Convenções do projeto

- Sem comentários de "what" óbvios. Comente só "why" não-óbvio.
- Testes não tocam rede ou LLM real (FakeOllama em `conftest.py`).
- `tg/` em vez de `telegram/` (este último colide com o pacote
  `python-telegram-bot`).
- Logs com símbolos `▶ ✓ ✖ •` em `core/pipeline.py`.
- Tipagem estrita em métodos públicos. `from __future__ import annotations`
  em todo módulo.
