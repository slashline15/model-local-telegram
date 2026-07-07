# Plano — Dual RAG + ACL simplificado + Fornecedores globais

Data: 2026-06-22  
Status: **implementado em 2026-07-07** (145 testes verdes). Um desvio
consciente: índices FAISS separados por obra (`faiss_{id}.index`) foram
adiados — o filtro por `project_id` no SQLite já garante o isolamento e a
separação física não muda comportamento visível. O índice local continua
único (`faiss.index`); o global é novo (`faiss_global.index`). Detalhes no
changelog do `ROADMAP.md`.

---

## Contexto e decisões de design

Este documento registra três decisões arquiteturais tomadas em conjunto:

1. **Dual RAG** — base global de nicho (construção/engenharia) + base individual por obra
2. **ACL simplificado** — segurança por decisão consciente de indexar, não por filtro automático
3. **Fornecedores globais** — tabela global com lookup Receita Federal; `empresas` por obra referencia por FK

---

## Parte 1 — Dual RAG

### Decisão

Duas instâncias FAISS operando em paralelo:

| Índice | Escopo | Quem popula | Filtro de acesso |
|--------|--------|-------------|-----------------|
| `faiss_global` | Instância única, todo o sistema | Admin do sistema via CLI/script | Nenhum — qualquer membro de qualquer obra acessa |
| `faiss_project` | Uma instância por `project_id` | Interações e documentos indexados da obra | `project_id` (isolamento de obra) |

O peso relativo entre os dois índices é configurável por projeto (`projects.global_rag_weight`, default 0.5). O admin pode ajustar: obra com muito histórico próprio → baixa o peso global; obra nova sem histórico → sobe o peso global.

### Schema — novas tabelas

```sql
-- Chunks da base global (normas, vocabulário técnico, referências de nicho)
CREATE TABLE IF NOT EXISTS global_chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT    NOT NULL,  -- 'manual' | 'norma_abnt' | 'glossario' | 'inpi' | etc.
    doc_class   TEXT    NOT NULL DEFAULT 'norma',
    titulo      TEXT,
    conteudo    TEXT    NOT NULL,
    weight      REAL    NOT NULL DEFAULT 1.0,
    ativo       INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_global_chunks_source ON global_chunks(source);
CREATE INDEX IF NOT EXISTS idx_global_chunks_ativo  ON global_chunks(ativo);
```

### Schema — coluna nova em `projects`

```sql
-- Peso do índice global na busca contrastiva (0.0 = ignora global, 1.0 = mesmo peso)
ALTER TABLE projects ADD COLUMN global_rag_weight REAL NOT NULL DEFAULT 0.5;
```

Adicionada em `_LEGACY_MIGRATIONS` como:
```python
("projects", "global_rag_weight", "REAL NOT NULL DEFAULT 0.5"),
```

### Arquivos FAISS em disco

```
data/
  faiss_global.index          ← índice global único
  faiss_global_ids.json       ← mapeamento posição → global_chunk_id
  faiss_1.index               ← índice do project_id=1
  faiss_1_ids.json
  faiss_2.index               ← índice do project_id=2
  faiss_2_ids.json
  ...
```

`FaissManager` não muda — já é genérico o suficiente. O que muda é a instanciação: em vez de um único `FaissManager`, o bot mantém um `faiss_global` + um dict `faiss_by_project: dict[int, FaissManager]`.

### Mudança em `ContrastiveRAG`

`build()` recebe dois parâmetros novos opcionais:

```python
async def build(
    self,
    user_message: str,
    *,
    user_id: int | None = None,
    project_id: int | None = None,
    global_faiss: FaissManager | None = None,      # novo
    global_chunks_repo: GlobalChunksRepo | None = None,  # novo
    global_weight: float = 0.5,                    # novo
    ...
) -> RagBundle:
```

Fluxo de busca com dual RAG:

```
1. Embed a query (igual antes)

2a. FAISS local  → top_k hits → resolve via ChunksRepo      → score_final = sim * chunk.weight
2b. FAISS global → top_k hits → resolve via GlobalChunksRepo → score_final = sim * chunk.weight * global_weight

3. Merge os dois conjuntos de hits
   - Deduplica por conteúdo (hash ou id)
   - Ordena por score_final

4. fetch_by_ids no SQLite (somente hits locais precisam de filtro de projeto)
   Hits globais: carregados diretamente de global_chunks (sem ACL)

5. Separa positivos/negativos/neutros como hoje
```

### Repositório `GlobalChunksRepo`

Arquivo: `database/repos/global_chunks.py`  
Operações: `insert`, `get_by_ids`, `list_active`, `set_ativo`, `bulk_insert`

### Script de população da base global

`scripts/populate_global_base.py` — lê arquivos Markdown/PDF de um diretório de conhecimento, chunka, embeda e insere em `global_chunks` + `faiss_global`. Roda offline, não depende do bot estar no ar.

### `reindex.py` — mudanças

Adicionar flag `--scope global|project|all` para reconstruir o índice correto. Por padrão (`--scope project`) reconstrói só os índices por projeto (comportamento atual).

---

## Parte 2 — ACL simplificado

### Decisão

> Tudo indexado fica disponível para membros da obra, independente do nível de acesso.  
> Documento sensível: o admin decide se indexa. Se indexar com valores omitidos, a versão sem valores é o que entra no RAG.  
> A segurança está na decisão consciente de indexar, não num filtro automático.

### O que muda no código

**`interactions.fetch_by_ids`** — remover o filtro de `visibilidade`:

```python
# ANTES
where += " AND (visibilidade = 'publica' OR user_id = ?)"

# DEPOIS — só isolamento por obra
# (linha removida; project_id continua filtrando)
```

O parâmetro `requester_user_id` deixa de ser usado para visibilidade e pode ser removido da assinatura em versão futura. Por ora, mantém-se para não quebrar testes.

**`anotacoes.visibilidade`** — mantida, mas com semântica diferente: é escolha pessoal do usuário de não compartilhar uma nota com a equipe. Não afeta o RAG da obra — anotações privadas simplesmente não são indexadas.

**`doc_classes` — `nivel_min_ler`** — coluna permanece no schema (pode ser útil para auditoria), mas o RAG para de consultá-la na busca. O `/doc` handler usa `nivel_min_classificar` apenas para decidir se o usuário pode fazer o upload, não para filtrar quem lê.

**`/doc` handler** — adicionar confirmação para classes sensíveis:

```
Classes que pedem confirmação antes de indexar:
  folha_pgto, planilha_orcamento, contrato, proposta
  
Mensagem: "⚠️ Planilha orçamentária — este documento ficará disponível para 
           todos os membros da obra após indexação. Confirmar? [Sim / Não]"
```

**`documents.visibilidade`** — coluna permanece. Default continua `'publica'`. O admin pode marcar um documento como `'privada'` para arquivar sem indexar no RAG — nesse caso o chunk nunca é criado.

### O que NÃO muda

- Isolamento por `project_id` permanece em `fetch_by_ids` e `list_user_history`
- `project_members` continua sendo a fonte de autoridade de quem é membro
- `middleware.py` continua verificando membership antes de qualquer operação

---

## Parte 3 — Fornecedores globais

### Decisão

Duplicação entre obras é esperada para terceirizados. Resolver com:
- **Tabela global `fornecedores`**: catálogo canônico, CNPJ como chave natural
- **`empresas` por obra**: continua existindo, adiciona FK opcional para `fornecedores`
- **Medições e notas**: ficam locais em `efetivo_diario`, `materiais_movimento`, `anotacoes` — não vazam entre obras

### Schema — nova tabela `fornecedores`

```sql
CREATE TABLE IF NOT EXISTS fornecedores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cnpj            TEXT    UNIQUE NOT NULL,
    razao_social    TEXT    NOT NULL,
    nome_fantasia   TEXT,
    tipo_atividade  TEXT,               -- 'servicos' | 'materiais' | 'ambos' | 'outro'
    situacao_rf     TEXT,               -- 'Ativa' | 'Baixada' | 'Inapta' | etc.
    fonte           TEXT    NOT NULL DEFAULT 'manual',  -- 'manual' | 'receita_federal'
    dados_rf        TEXT,               -- JSON blob completo da Receita (opcional)
    consultado_em   TEXT,               -- ISO timestamp da última consulta à RF
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fornecedores_cnpj ON fornecedores(cnpj);
```

### Mudança em `empresas`

```sql
-- migration legacy
ALTER TABLE empresas ADD COLUMN fornecedor_id INTEGER REFERENCES fornecedores(id);
```

Adicionada em `_LEGACY_MIGRATIONS`:
```python
("empresas", "fornecedor_id", "INTEGER REFERENCES fornecedores(id)"),
```

`EmpresasRepo.create()` — se `cnpj` informado, tentar `auto_link_fornecedor()`:
1. Busca em `fornecedores` por CNPJ
2. Se encontrado: seta `fornecedor_id`
3. Se não encontrado e `lookup_receita=True`: consulta API, insere em `fornecedores`, seta FK

### Repositório `FornecedoresRepo`

Arquivo: `database/repos/fornecedores.py`  
Operações: `get_by_cnpj`, `create`, `update_from_rf`, `list_all`, `search_by_nome`

### Integração Receita Federal

Arquivo: `core/receita_client.py`

API pública, sem autenticação:
```
GET https://publica.cnpj.ws/cnpj/{cnpj_limpo}
```

Retorna JSON com `razao_social`, `nome_fantasia`, `situacao_cadastral`, `cnae_fiscal_descricao`, etc.

```python
async def lookup_cnpj(cnpj: str) -> dict | None:
    """Consulta CNPJ na Receita Federal. Retorna None se não encontrado."""
    cnpj_limpo = re.sub(r"\D", "", cnpj)
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://publica.cnpj.ws/cnpj/{cnpj_limpo}",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status != 200:
                return None
            return await r.json()
```

Resultado salvo em `fornecedores.dados_rf` (TEXT JSON) e campos relevantes desnormalizados nas colunas próprias.

### Relação obra → fornecedor (medições)

`efetivo_diario.empresa_id` referencia `empresas` (local), que por sua vez pode referenciar `fornecedores` (global). Para queries de consolidação entre obras:

```sql
-- Quantas obras usaram a empresa X?
SELECT e.project_id, COUNT(*) as dias_trabalhados
FROM efetivo_diario ed
JOIN empresas e ON ed.empresa_id = e.id
WHERE e.fornecedor_id = ?
GROUP BY e.project_id;
```

Isso é feito na camada de relatório/dashboard — não no fluxo do bot.

---

## Passos de implementação (ordem sugerida)

| # | Passo | Arquivos tocados | Dependências |
|---|-------|-----------------|--------------|
| 1 | Schema: `global_chunks` + `fornecedores` + migration `empresas.fornecedor_id` + `projects.global_rag_weight` | `database/schema.py` | — |
| 2 | `GlobalChunksRepo` + `FornecedoresRepo` | `database/repos/` | Passo 1 |
| 3 | `receita_client.py` + lookup no `EmpresasRepo.create()` | `core/`, `database/repos/empresas.py` | Passo 2 |
| 4 | Instanciação dual FAISS em `tg/bot.py` (global + dict por projeto) | `tg/bot.py` | Passo 1 |
| 5 | `ContrastiveRAG.build()` com merge dual | `llm/contrastive_rag.py` | Passo 4 |
| 6 | Remover filtro `visibilidade` de `fetch_by_ids`; confirmação no `/doc` | `database/repos/interactions.py`, handler `/doc` | — |
| 7 | `scripts/populate_global_base.py` | `scripts/` | Passos 2, 4 |
| 8 | `reindex.py` — flag `--scope` | `scripts/reindex.py` | Passo 4 |
| 9 | Testes: `test_dual_rag.py`, `test_fornecedores_repo.py`, `test_receita_client.py` | `tests/` | Todos anteriores |

---

## Decisões em aberto (não bloqueiam implementação)

- **Tamanho máximo da base global**: ainda indefinido. Começa pequeno (normas ABNT relevantes, glossário técnico) e cresce conforme uso.
- **Cache de consulta à Receita Federal**: a API pública tem rate limit. Considerar TTL de 30 dias no campo `consultado_em` para não repetir consultas desnecessárias.
- **`faiss_by_project` em memória**: se o bot reiniciar com muitas obras ativas, carregar todos os índices pode ser custoso. Considerar lazy loading com LRU cache de N projetos mais recentes.
- **`global_rag_weight` editável pelo usuário**: hoje é por projeto. Poderia ser por usuário (settings). Deixar na tabela `projects` por ora.
