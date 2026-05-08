## Problema do fallback cego

> Esta é a nota-âncora do desbloqueio. Tudo que importa hoje passa por aqui.

### Diagnóstico em uma frase
O exemplo negativo é apresentado à IA como um **bloco inteiro** rotulado "evite isso", sem dizer **o que** dele estava errado. A IA aprende a evitar o conjunto, não a falha.

### Como o problema aparece na prática
Imagine um exemplo negativo:

```
Pergunta: "Choveu forte das 10h às 14h, paramos a terraplanagem."

Resposta (rotulada negativa):
  Clima: chuva
  Anotações: paralisação por chuva
```

O que estava errado? **Faltou vincular a Anotação à atividade `Terraplanagem` e registrar o gap de 4h** (ver [[Campos do RDO]]). Mas a IA recebe só o rótulo "evite". Ela pode aprender:

- ❌ "evite registrar clima chuvoso" (errado — clima estava certo)
- ❌ "evite respostas curtas" (irrelevante)
- ❌ "evite anotação de chuva" (oposto do que se quer)

Em vez de:
- ✅ "anotação de chuva precisa vincular atividade impactada e duração"

### Por que isso bloqueia o avanço
1. **Teto do scoring atual** — afinar prompt e modelo não passa de ~60% porque o sinal de aprendizado é ambíguo
2. **Poluição do índice** — quanto mais negativos sem rótulo de causa, mais ruído no RAG
3. **Recall confuso** — o FAISS pode trazer um negativo cuja parte negativa nem se aplica ao caso atual

### O que seria preciso para fechar o gap até 90%
O sinal precisa carregar **causa**, não só **rótulo**. Em outras palavras:

- Negativo ≠ "esta resposta toda é ruim"
- Negativo = "**neste ponto específico** desta resposta, faltou X / sobrou Y / errou Z"

→ A escolha de **como** capturar essa causa é o que [[Hipóteses de solução]] discute.

### Sintomas a observar quando isso for resolvido
- Curva de acerto rompendo o platô dos 60%
- Menos contradição entre exemplos similares no RAG
- Anotações com vinculação de atividade subindo (KPI direto do RDO)

Ver: [[Treinamento contrastivo]] · [[Hipóteses de solução]] · [[Próximos passos]]
