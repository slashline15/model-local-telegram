## Hierarquia de documentos

Sem critério, RAG vira leilão de quantidade. Esta nota define o sistema de pesos que evita que transcrições de reunião sufoquem contratos no recall.

### Tabela de classes (peso base)

| Classe | Peso | Quando usar |
|--------|------|-------------|
| `contract` | 1.5 | Contratos de empreita, aditivos contratuais |
| `spec` | 1.4 | Memorial descritivo, projeto executivo, especificação técnica |
| `norm` | 1.3 | NRs, normas técnicas, regulamento interno |
| `proposal` | 1.1 | Propostas comerciais, escopo preliminar |
| `note` | 1.0 | Anotações livres de campo (default para texto) |
| `meeting` | 0.7 | Transcrições de reunião, áudios longos sem estrutura |
| `other` | 0.8 | Quando não classificado |

### Boost por papel do remetente

| Nível | Boost |
|-------|-------|
| N1 (admin) | +0.2 |
| N2 (co-responsável) | +0.1 |
| N3 (operacional) | 0 |

> Racional: o admin filtra. O capacete branco joga tudo. Confiar mais no primeiro alinha o RAG ao mundo real do canteiro.

### Aplicação no retrieval

```python
score_final = score_similarity * class_weight * sender_boost
# aplicar antes do top-k cut
```

### Como o usuário declara a classe

1. **Botões inline** (preferencial) — bot pergunta no momento do upload
2. **Caption** — `#contrato`, `#memorial`, `#reuniao` no upload
3. **Auto-classificação** — só se 1 e 2 gerarem atrito

### Schema mínimo

```sql
CREATE TABLE interaction_chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    interaction_id  INTEGER NOT NULL REFERENCES interactions(id),
    chunk_idx       INTEGER NOT NULL,
    content         TEXT NOT NULL,
    doc_class       TEXT NOT NULL DEFAULT 'note',
    weight          REAL NOT NULL DEFAULT 1.0,
    created_at      TEXT NOT NULL
);
```

> Pesos finais (incluindo boost) podem ser pré-calculados na inserção, evitando JOINs caros no retrieval.

Ver: [[Análise — Chunking + Tokens + Bot Debug]] · [[Níveis de acesso]]
