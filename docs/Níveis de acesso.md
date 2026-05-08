## Níveis de acesso

Hierarquia de permissões. Cada nível herda tudo do nível inferior.

> Isolamento é absoluto: middleware deve checar `project_members` antes de qualquer leitura de dado de obra. Ver [[ROADMAP]] (Fase 2).

---

### Nível 1 — ADMIN
Engenheiro, gerente, cliente contratante. Quem assina e oficializa.

**Exclusivas do N1:**
- Assinar diários
- Cadastro de obras
- Acesso aos logs de edição/exclusão
- Cadastrar/bloquear usuários N2
- Delegar permissões para supervisão
- Aprovar cronograma macro

### Nível 2 — CO-RESPONSÁVEL
Auxiliar, engenheiro, trainee, apontador. Supervisão/administração.

**Exclusivas do N2:**
- Cadastrar/bloquear usuários operacionais
- Cadastrar empresas e frentes de serviço
- Inserir dados retroativos
- Editar ou excluir registros
- Criar cronograma macro e planejamento semanal
- Delegar permissões
- Aprovar registros de equipamentos/materiais
- Cadastro de funções fixas

### Nível 3 — OPERACIONAL
Capacetes brancos: mestres, encarregados, técnicos de segurança, estagiários, gestores de campo. Linha de frente.

**Permissões base:**
- Registrar atividades, efetivo, materiais, clima, anotações
- Ocorrências (coletivas e privadas — privadas só admin vê)
- Fotos e comentários
- Solicitar material, formar equipes, frentes de trabalho
- Programar atividades (depende de aprovação)
- Acessar diários **não assinados**

---

![[Pasted image 20260507212721.png]]
