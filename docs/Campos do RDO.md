## Campos do RDO

Os campos mínimos obrigatórios. A IA usa estas regras para decidir **onde registrar o quê**.

> Meta de acerto: **90%**. Hoje: **~60%**. O destrava está no [[Problema do fallback cego]].

---

### 1. Atividades
Tarefas executadas no período. Devem se relacionar ao **cronograma planejado**.

Estados que a IA classifica: `Concluída` · `Em andamento` · `Atrasada` · `Impedida`.

Métrica: planejado vs. executado.

### 2. Efetivo
Pessoal alocado, por especialidade. Cálculo: soma de presentes por função.

A IA deve sinalizar gap quando o efetivo do dia for muito menor que o necessário pra atividade prevista — risco de atraso.

### 3. Clima
Apenas: `Sol` · `Nublado` · `Chuva`.

Quando `Chuva`, a IA dispara automaticamente detalhamento em **Anotações** (impacto, atividades paralisadas, horário).

### 4. Expediente
Início, término e regime de turno. Atividades fora desse intervalo viram "Hora extra" ou "Turno especial".

### 5. Materiais e Equipamentos
Insumos e maquinário em operação ou parados. Importante: rastrear **chegada de materiais de responsabilidade do cliente** (gera Ocorrência se atrasar).

### 6. Anotações — o campo crítico
**É o coração jurídico do RDO.** Transforma observação bruta em evidência.

Toda anotação deve ter:
- **Temporalidade**: data/hora de início e fim (ou "em aberto")
- **Vínculo**: amarrada a uma Atividade ou Recurso
- **Impacto**: a IA deve extrair a consequência (atraso, aditivo contratual, etc.)

Natureza:
- **Evento** — esperado ("concretagem conforme planejado")
- **Ocorrência** — anômalo ("atraso na entrega do aço")

> Nunca truncar, nunca limitar. É o que salva construtoras em disputas.

---

### Exemplos de roteamento (entrada → destino)

| Mensagem do usuário | Destino | Detalhe |
|---------------------|---------|---------|
| "Choveu forte das 10h às 14h, paramos a terraplanagem." | Clima → Anotações | Chuva no clima; Ocorrência em anotações vinculada a `Terraplanagem`, gap 4h |
| "Chegaram 5 caminhões de areia, mas o cliente não mandou o cimento." | Materiais → Anotações | Entrada de areia; Ocorrência "Atraso de material do cliente" |
| "Temos 10 pedreiros e 4 serventes hoje." | Efetivo | Atualiza contador por categoria |

Ver: [[Registros]] · [[Logs do bot (exemplo)]] · [[ROADMAP]]
