# Grupo 15
**Integrantes:** Rafael Almeida Ferreira e Guilherme Silva Xavier

Motor antifraude de Pix do Hackathon QI Tech 2026. A API recebe um Pix, compara com o histórico daquele cliente e daquele destino, e devolve uma decisão — `approved`, `two_factor_required` ou `rejected` — com o motivo de cada ponto de risco.

Ela não move dinheiro: quem envia ou cancela o Pix é o banco. O antifraude decide e explica.

---

## Comece aqui

```bash
docker compose up -d --build
```

Com a API no ar em `http://localhost:3000`, use:

- `POST /pix/transaction` para analisar e registrar um Pix;
- `GET /pix/transaction/{id}` para consultar a análise gravada;
- `POST /pix/transaction/{id}/two-factor` para informar o resultado do segundo fator;
- `docker compose --profile tests run --build --rm tests` para executar a suíte.

O exemplo completo de requisição, os códigos HTTP e a configuração estão em [Como rodar e testar](#como-rodar-e-testar).

**Navegação:** [Decisão](#como-o-motor-de-decisão-funciona) · [Regras](#regras) · [2FA e risco](#como-as-regras-viram-decisão) · [Execução e contrato](#como-rodar-e-testar)

---

## Como o motor de decisão funciona

### A tese

**Cada Pix é comparado com o histórico do próprio cliente.** A pergunta não é "isso é estranho?", é "isso é estranho *para esta pessoa*?". Um Pix às 3h da manhã é normal para quem trabalha de madrugada e estranho para quem sempre pagou de dia; R$ 5.000 é o aluguel de um cliente e dez vezes o ticket de outro.

Daí saem os três princípios que organizam o motor:

1. **Nenhuma métrica decide sozinha.** O risco é a soma de cinco métricas, e a maior delas vale 35 — abaixo dos 40 em que começa o 2FA. Isso não é só uma intenção: é um `CHECK` na tabela `risk_metric`.
2. **O horário é a métrica que mais pesa.** O celular roubado é usado quando o dono não usaria; isso diz mais do que qualquer outro comportamento.
3. **O risco tem prazo de validade.** Um Pix arriscado deixa parte do risco para os Pix seguintes do mesmo cliente por 24 horas — e depois vence.

E duas exceções, ditas em voz alta, porque nenhuma delas é métrica:

- **Fatos reprovam direto.** Documento que não existe, lista restritiva e CPF de pessoa falecida na Receita. Nenhuma métrica de *comportamento* decide sozinha; fatos objetivos, sim.
- **O Modo Rua pede 2FA direto.** Longe de todos os lugares de costume do cliente, o motor não tem como saber se é ele: pede a selfie, e aprende o lugar quando ela é confirmada. Não soma ponto e nunca reprova — só transforma um `approved` em `two_factor_required`. É uma pergunta de autenticação ("é você?"), e não uma medida de comportamento ("isso é estranho?").

### O caminho de um Pix

```
POST /pix/transaction
  │
  ├─ 1. o mesmo id já chegou?      mesmo conteúdo → devolve a análise original (200)
  │                                 conteúdo diferente → 409
  ├─ 2. normaliza                   documentos só com dígitos, datas com fuso
  ├─ 3. consulta a Receita          situação de quem recebe; prazo de 0,5 s, cache de 24 h
  │                                 (ANTES da trava: a trava nunca espera a rede)
  ├─ 4. trava o pagador             Pix simultâneos do mesmo pagador passam um por vez
  ├─ 5. junta os fatos              o histórico de 90 dias do pagador, do destino, do aparelho, do IP
  ├─ 6. mede                        sinais → métricas (com teto) → risco deste Pix
  ├─ 7. herda                       o risco ainda válido dos Pix das últimas 24 h
  ├─ 8. decide                      fato? reprova. Senão, pelas faixas do risco
  │                                 aprovado longe de casa? o Modo Rua pede 2FA
  └─ 9. grava tudo                  o Pix, a decisão, cada sinal — inclusive quando reprova
```

O motor (`src/controllers/risk_rules.py`) é feito de **funções puras**: o controller busca os números no banco, e cada sinal só compara. Por isso a mesma entrada dá sempre a mesma decisão.

Os **pontos de cada sinal** e o **teto de cada métrica** moram no banco (tabelas `rule` e `risk_metric`): mudam com um `UPDATE`, sem deploy. Os **limiares** (3x o ticket, 30 km, 10 minutos) moram no código, ao lado da conta que os usa.

As camadas seguem o Base Service: o **resource** só valida o corpo (schema), chama o controller e escolhe o status HTTP; o **controller** decide e é o único que dá `commit`, sempre depois de todas as checagens; o **repository** é a única porta do banco e não decide nada — até a definição de "Pix confiável" vem do motor como parâmetro; o **DTO** escolhe o que sai na resposta, e o model nunca sai direto.

### O escopo: o que fica de fora e o que vai além

O desafio pede o `POST` e o `GET` de `/pix/transaction`, e deixa de fora outras APIs. Duas peças vão além disso, de propósito:

- **`POST /pix/transaction/{id}/two-factor`.** Pedir 2FA só faz sentido se o resultado volta. Sem esta rota, o motor não distingue quem confirmou a selfie de quem falhou, e o risco herdado não teria como punir o ladrão que insiste (ver [O risco herdado](#o-risco-herdado)). Ela não decide nada novo: só fecha a pendência que o próprio `POST` abriu.
- **A consulta à Receita.** Não é uma API que construímos para o produto: é um **mock** de um serviço externo, para mostrar como o motor consulta uma base de fora sem depender dela. A consulta tem prazo de 0,5 s, cache de 24 h e nunca reprova por falha — a resposta ao "não dá para esperar uma consulta externa pesada". Com `RECEITA_ENABLED=false`, o motor roda sem ela.

Cadastro de contas e front-end ficam de fora: a conta chega pronta no payload.

### O que é "normal" para o cliente

Só **Pix confiáveis** ensinam o normal: aprovados direto, ou com 2FA confirmado. É com eles que o motor aprende o horário de costume, o ticket mediano, os lugares de costume e os destinos conhecidos. O que o ladrão fez — reprovado, 2FA pendente ou falhado — nunca vira o normal de ninguém.

Já as **tentativas** contam todas, inclusive as reprovadas: a rajada do ladrão conta mesmo quando foi barrada.

Como o Modo Rua pede 2FA em todo Pix longe dos lugares de costume, um lugar novo só vira "de costume" depois de uma selfie confirmada. Nenhum Pix longe é aprovado direto — então nenhum ensina o lugar sozinho.

### Modelagem de dados

```
analysis_status ─┐                    risk_metric ──┐
                 ▼                                  ▼
pix_transaction ─1:1─► analysis ─1:N─► rule_result ◄─N:1─ rule
   │                     │
   │                     └─1:0..1─► two_factor_result
   │
   └─1:N─► pix_event ◄─N:1─ pix_event_type

restricted_party      (lista restritiva, consultada por documento)
document_situation    (cache das respostas da Receita)
```

| Tabela | O que guarda | Decisões |
|---|---|---|
| `pix_transaction` | O Pix como chegou | Documentos só com dígitos (o mesmo CPF com e sem máscara é o mesmo pagador). `pix_id` único. Hash do payload para reconhecer o reenvio |
| `analysis` | A decisão | **Nunca recebe UPDATE.** Guarda `transaction_risk`, `inherited_risk` e `risk_score` — e um `CHECK` garante que o terceiro é a soma dos dois, com teto de 100. `decision_reason` (`blocking_rule`, `risk_score` ou `two_factor_rule`) permite perguntar ao banco "quantos foram barrados por fato hoje?" ou "quantos 2FA o Modo Rua pediu?" |
| `rule_result` | Cada sinal que disparou, com o que viu | `points` é **copiado** do sinal no momento: se um peso mudar amanhã, as análises antigas continuam com o valor da época |
| `risk_metric` | As 5 métricas e o teto de cada uma | `CHECK (max_points < 40)`: nenhuma métrica decide sozinha |
| `rule` | Os sinais: pontos, liga/desliga, métrica, ação | Três ações: `score` (comportamento, pertence a uma métrica e vale ponto), `reject` (fato) e `two_factor` (Modo Rua). Fato e Modo Rua não têm métrica e não valem ponto (`CHECK`) |
| `two_factor_result` | A pendência do 2FA | Nasce `pending` com a análise, com prazo (`expires_at`), e fecha uma vez: `confirmed`, `failed` ou `expired`. Estado numa coluna só, para não existir "pendente e confirmado" ao mesmo tempo |
| `pix_event` | A linha do tempo de cada Pix: um evento por linha | **Só recebe INSERT.** Liga ao log da aplicação pelo `request_id`. Guarda o código QIT quando o pedido foi recusado (`CHECK` no formato). Não guarda payload, IP, aparelho, documento nem token |
| `pix_event_type` | Os 7 tipos de evento | Catálogo com chave estrangeira, como `analysis_status` |
| `analysis_status` | Os 3 status | Catálogo com chave estrangeira, no padrão do Base Service |
| `restricted_party` | Documentos com fraude confirmada, sanção ou ordem judicial | Local, consultado por índice: a decisão não espera a rede |
| `document_situation` | O que a Receita respondeu sobre cada documento | Cache de 24 h. Só resposta de verdade entra; falha da Receita não é guardada |

Cinco índices em `pix_transaction` cobrem as consultas do motor: toda métrica começa por "este pagador", "este destino", "este aparelho" ou "este IP", numa janela de tempo.

### A linha do tempo de cada Pix (`pix_event`)

A decisão fica em `analysis`; o que acontece com o Pix depois dela — ou o que não muda a decisão — fica em `pix_event`, um evento por linha:

| Evento | Quando | `details` |
|---|---|---|
| `receita_unavailable` | A Receita não respondeu; o Pix seguiu sem ela | o motivo (`timeout`, `http_500`…) |
| `analysis_created` | O Pix chegou e foi analisado | status, nota e motivo da decisão |
| `replay_returned` | O mesmo Pix chegou de novo (200) | — |
| `conflict_refused` | O mesmo id com outro conteúdo (409, `QIT002001`) | — |
| `two_factor_answered` | O banco informou o 2FA no prazo | `confirmed` ou `failed` |
| `two_factor_expired` | Resposta de 2FA depois dos 3 minutos (409, `QIT002006`) | o que foi enviado e o prazo |
| `two_factor_refused` | 2FA já respondido (`QIT002004`) ou Pix que não pediu 2FA (`QIT002003`) | o motivo e o que foi enviado |

**Dois logs, dois lugares.** O log técnico — toda requisição, inclusive 400, 403, 404 e o `/health_check` — continua só na saída da aplicação (`docker compose logs api`). No banco entra só o que aconteceu **com um Pix**: requisição que nem vira Pix é problema técnico, e gravá-la deixaria qualquer um encher a tabela mandando lixo. Os dois se ligam pelo `request_id`, que também volta no cabeçalho `X-Request-ID` da resposta.

**Quem grava é o controller**, porque só ele sabe qual evento aconteceu; o evento de criação entra na mesma transação da análise. Uma recusa (409) grava o evento e dá `commit` antes de devolver o erro — senão o rastro da recusa se perderia com o `rollback`. Ler (`GET`) nunca grava evento.

**Para que serve:** auditoria (a história de um Pix numa consulta só), antifraude (um 409 de conflito é alguém tentando trocar um Pix já decidido) e calibração (os 2FA confirmados e falhados ficam registrados com hora).

**Retenção (LGPD):** aqui o histórico fica para sempre, por ser um projeto de estudo. Em produção, o que passa de 90 dias sairia do banco — é a maior janela que o motor usa, e guardar IP, aparelho e localização além disso não serve a regra nenhuma.

---

## Regras

### As métricas

| Métrica | Teto | O que pergunta |
|---|---|---|
| Horário | 35 | O cliente costuma pagar nesta hora? |
| Valor | 30 | O valor cabe no ticket mediano dele? Passou do teto? E a soma do dia? |
| Velocidade | 20 | Quantos Pix ele fez nos últimos minutos? |
| Destino | 15 | Ele já pagou esta conta? Ela parece laranja? |
| Origem | 10 | O aparelho e o IP batem com a conta? |

Os sinais de uma mesma métrica somam, mas nunca passam do teto dela.

### As cinco famílias do desafio

| Família | Pergunta | Regras |
|---|---|---|
| Limite | Passou do teto? | `night_limit_exceeded`, `high_amount_without_history` |
| Velocity | Rápido demais? | `burst_4_in_10_minutes`, `burst_3_in_10_minutes` |
| Montante | Somou demais? | `day_sum_3x_profile`, `day_sum_without_history` |
| Perfil | Está no padrão do cliente? | `unusual_hour`, `rare_hour`, `night_without_history`, `amount_*_profile`, `device_changed`, e o Modo Rua (`street_mode`), que pede 2FA |
| Contraparte | Quem recebe é confiável? | `new_destination`, `new_destination_account`, `destination_many_payers`, `receita_destination_irregular`, `restricted_destination`, `receita_destination_blocked` |

**Limite e Montante moram na métrica Valor, e não em métricas próprias.** As três perguntas (o valor cabe no ticket? passou do teto? a soma do dia passou do normal?) olham para o mesmo dinheiro. Em métricas separadas, um único Pix de R$ 5.000 à noite pontuaria três vezes pelo mesmo real e reprovaria sozinho, quebrando o princípio de que nenhuma métrica decide sozinha. Juntas, elas somam até o teto de 30.

### Os sinais

| Regra | Lógica | Parâmetros | Por quê |
|---|---|---|---|
| **Horário** | | | |
| `unusual_hour` (35) | O cliente nunca fez Pix nesta faixa de horário | Faixa = a hora do Pix ±1h; janela de 90 dias; mínimo de 5 Pix confiáveis | O celular roubado é usado quando o dono não usaria. ±1h para quem paga às 14h não ficar "fora do padrão" às 15h |
| `rare_hour` (20) | Menos de 10% dos Pix do cliente nesta faixa | 10% | Horário que acontece, mas é exceção |
| `night_without_history` (20) | Cliente sem histórico de horários, Pix entre 20h e 6h | 20h–6h; menos de 5 Pix confiáveis | Sem histórico, vale a referência de todo mundo: o período noturno do Banco Central (Resolução BCB nº 142/2021) |
| **Valor** | | | |
| `amount_5x_profile` (30) | Valor ≥ 5x o ticket mediano | Mínimo de 3 Pix confiáveis | Com menos de 3 Pix, a régua não diz nada. **Mediana, e não média:** um único Pix fora da curva (a compra do carro) puxaria a média para cima e deixaria o cliente imune a esta regra por 90 dias; a mediana não se mexe |
| `amount_3x_profile` (20) | Valor entre 3x e 5x o ticket mediano | | Degraus, para que "um pouco acima" pese menos que "muito acima" |
| `amount_2x_profile` (10) | Valor entre 2x e 3x o ticket mediano | | |
| `high_amount_without_history` (15) | Cliente sem ticket mediano, Pix acima de R$ 1.000 | R$ 1.000 | Sem histórico, o limite noturno do Banco Central serve de referência de "valor alto" |
| `night_limit_exceeded` (20) | **Limite.** Pix de pessoa física entre 20h e 6h, acima de R$ 1.000, e o cliente nunca fez um Pix noturno confiável acima disso | R$ 1.000; 20h–6h; só CPF; mínimo de 3 Pix confiáveis; 90 dias | R$ 1.000 é o limite noturno padrão do Banco Central para pessoa física (Resolução BCB nº 142/2021). O ticket mediano não pega esse caso: para quem paga R$ 800 de costume, R$ 1.500 é só 1,9x. Quem já pagou acima do teto à noite, com Pix confiável, provou que aumentou o limite e não é punido. Sem histórico, `high_amount_without_history` já usa os mesmos R$ 1.000, por isso a regra não roda (contaria o mesmo fato duas vezes) |
| `day_sum_3x_profile` (20) | **Montante.** A soma das últimas 24 h (Pix confiáveis + este) passou de 3x o total diário médio do cliente | 3x; 24 h; mínimo de 3 dias com Pix confiável em 90 dias; ao menos 2 Pix na janela; soma acima de R$ 1.000 | Pega o golpe que **fraciona** o valor para nenhum Pix parecer grande sozinho. O total diário médio só conta dias com Pix, e só antes da janela, para o golpe em curso não inflar a própria régua. Abaixo de R$ 1.000 no dia, a soma não pesa: não compensa a fricção |
| `day_sum_without_history` (15) | **Montante.** Cliente sem total diário médio, soma das últimas 24 h acima de R$ 1.000 | R$ 1.000; 24 h; ao menos 2 Pix na janela | Sem histórico, a mesma referência de R$ 1.000: dez Pix de R$ 900 somam R$ 9.000, mas nenhum deles passaria em `high_amount_without_history` |
| **Velocidade** | | | |
| `burst_4_in_10_minutes` (20) | 4 ou mais Pix do cliente em 10 minutos | Conta este Pix e os reprovados | O ladrão tem pressa: sabe que a vítima vai bloquear o celular |
| `burst_3_in_10_minutes` (10) | 3 Pix em 10 minutos | | Três amigos devolvendo a conta do bar já dão três: é só um sinal |
| **Destino** | | | |
| `new_destination` (5) | O cliente nunca pagou esta conta | Pix confiáveis em 90 dias | Destino novo é o normal de muita coisa — vale pouco sozinho |
| `new_destination_account` (10) | Conta de destino aberta há menos de 30 dias | 30 dias | Conta laranja costuma ser aberta para o golpe |
| `destination_many_payers` (10) | A conta recebeu de 5 ou mais pagadores em 24 h | 5 pagadores; 24 h | Muitas vítimas pagando a mesma conta |
| `receita_destination_irregular` (10) | CPF/CNPJ de quem recebe suspenso ou pendente na Receita | | Tem caso legítimo (quem não declarou o imposto): é sinal, não fato |
| **Origem** | | | |
| `device_changed` (5) | Aparelho diferente do último Pix, ou outro aparelho nas últimas 24 h | 24 h | Premissa do projeto: um aparelho por conta. Trocar de celular é legítimo, mas é o momento do golpe da troca de aparelho |
| `device_shared` (10) | O mesmo aparelho usado por outro pagador | 30 dias | Quebra a premissa de uma conta por aparelho: app adulterado ou identificador clonado |
| `ip_shared` (10) | O mesmo IP usado por outro pagador | 24 h | Sinal fraco — operadoras põem milhares de clientes atrás do mesmo IP —, por isso a janela curta e o teto 10 |

### Os fatos: reprovam direto

| Regra | Lógica | Por quê |
|---|---|---|
| `invalid_document_number` | CPF/CNPJ de quem paga ou recebe com dígito verificador errado | Documento que não pode existir é payload adulterado. Reprova e fica gravado, em vez de virar 4xx |
| `account_opened_after_pix` | A conta de quem paga ou de quem recebe foi aberta depois da data do Pix | Ninguém paga de uma conta que ainda não existe, nem recebe nela: é payload adulterado. Pelo mesmo motivo do documento inválido, reprova e fica gravado — todo Pix que chega fica consultável |
| `restricted_payer` | Quem paga está na lista restritiva | Fraude confirmada, sanção ou ordem judicial |
| `restricted_destination` | Quem recebe está na lista restritiva | Idem |
| `receita_destination_blocked` | Na Receita, o documento de quem recebe não existe, foi cancelado, é nulo ou é de titular falecido | Ninguém legítimo recebe Pix num CPF assim: é o documento clássico de conta laranja |

### O Modo Rua: pede 2FA

| Regra | Lógica | Parâmetros | Por quê |
|---|---|---|---|
| `street_mode` | Pix a mais de 30 km de **todos** os lugares de costume do cliente pede 2FA, mesmo com risco baixo. Não soma ponto e nunca reprova | 30 km; lugares de costume = de onde saíram os Pix confiáveis (aprovados ou com 2FA confirmado) dos últimos 90 dias; a distância é até o lugar **mais perto** | O celular roubado é usado longe da rotina do dono. 30 km cobre uma região metropolitana: ir de São Paulo a Guarulhos não é viagem. Mais perto, e não a média: quem mora em São Paulo e trabalha em Campinas teria a média no meio da estrada, e todo Pix dele pediria 2FA. Sem geolocalização (é opcional) ou sem lugar de costume, não roda |

- **Quem viaja faz uma selfie por cidade.** O primeiro Pix em Salvador pede 2FA; confirmado, Salvador vira lugar de costume, e os próximos Pix lá seguem sem 2FA.
- **2FA pendente, expirado ou falhado não ensina lugar nenhum.** Senão, o ladrão transformaria a cidade dele em "de costume" — e o Modo Rua continua pedindo a selfie a cada Pix dele.
- **Por que não é uma métrica de pontos.** Como métrica (já foi uma, de 25 pontos), longe sozinho aprovava — e o primeiro Pix aprovado longe ensinava o lugar ao ladrão. Longe dos lugares de costume, a pergunta não é "quanto isso é estranho?", é "é você?". Quem responde é a selfie, não a soma.
- **O Modo Rua só sobe, nunca desce.** Um Pix já reprovado (por fato ou por pontos) continua reprovado; ele só transforma um `approved` em `two_factor_required`.

---

## Como as regras viram decisão

### A ordem

```
1. Algum fato disparou?                  → rejected
2. risco = risco deste Pix + risco herdado, com teto de 100
       0 a 39   → approved
      40 a 69   → two_factor_required
      70 a 100  → rejected
3. Saiu approved, mas o Modo Rua disparou? → two_factor_required
```

Duas métricas fortes pedem 2FA; reprovar exige três. Exemplos:

| Situação | Conta | Decisão |
|---|---|---|
| Aluguel de R$ 5.000, destino de sempre, horário de sempre | valor 30 | `approved` |
| Viagem: Pix de dia, em outra cidade | destino novo 5 + **Modo Rua** | `two_factor_required` — uma selfie; confirmada, a cidade vira de costume |
| Viagem, no dia seguinte, na mesma cidade, depois da selfie confirmada | destino novo 5 | `approved` |
| Valor alto para conta aberta há 5 dias | valor 30 + destino 15 | `two_factor_required` |
| Às 3h (nunca pagou nesse horário), valor 6x o ticket, para conta aberta há 5 dias | horário 35 + valor 30 + destino 15 | `rejected` |
| Às 3h, de outra cidade, valor 6x o ticket, destino de sempre | horário 35 + valor 30 = 65 + **Modo Rua** | `two_factor_required`. Se a selfie falhar, o próximo Pix herda os 65 e reprova com 5 pontos a mais |
| Fracionamento: 6 Pix de R$ 900 à noite, no mesmo segundo, de cliente sem histórico | 1º: horário 20 + destino 5. 2º: + soma do dia 15 + herança 12. 3º em diante: + velocidade e herança | 1º `approved`, 2º `two_factor_required`, demais `rejected` |

### Quando o motor pede 2FA

Quando o risco fica entre 40 e 69, ou quando o Modo Rua dispara num Pix que sairia aprovado: há dúvida, e o cliente confirma que é ele mesmo. Quem pede o segundo fator (a selfie, a biometria) é o banco; o antifraude só decide quando pedir.

O 2FA pedido nasce como **pendência com prazo de 3 minutos**. O banco informa o resultado em `POST /pix/transaction/{id}/two-factor`. A decisão original nunca muda; o que muda é a pendência:

| Resposta do banco | Efeito |
|---|---|
| `confirmed`, dentro do prazo | A dúvida foi tirada: o risco desse Pix não passa adiante, e o lugar e o destino viram "de costume" |
| `failed`, dentro do prazo | A dúvida virou suspeita: o risco desse Pix passa **inteiro** para o próximo |
| Qualquer resposta depois dos 3 minutos | Recusada com 409 (`QIT002006`); a pendência fecha como `expired` |

### O risco herdado

O risco de um Pix vale por 24 horas. O próximo Pix do mesmo cliente herda o **maior** risco ainda válido, conforme o que aconteceu no 2FA:

| O Pix anterior | Quanto deixa de herança |
|---|---|
| 2FA confirmado | Nada |
| 2FA falhou | Tudo |
| Qualquer outro (aprovado, pendente, reprovado) | Metade |

A herança vem sempre do risco **do próprio** Pix anterior, nunca do que ele mesmo herdou — senão o risco cresceria sozinho, Pix após Pix. É isso que pega quem insiste: o ladrão que falhou na selfie e tenta de novo herda o risco inteiro, e é reprovado.

### O exemplo do desafio

O JSON do desafio, com um pagador sem histórico: R$ 1.500 às 22:15, para uma conta aberta há 7 dias.

| Métrica | Pontos | Por quê |
|---|---|---|
| Horário | 20 | Noite, cliente sem histórico de horários |
| Valor | 15 | Acima de R$ 1.000, cliente sem ticket mediano |
| Destino | 15 | Destino novo (5) + conta de 7 dias (10) |
| **Risco** | **50** | **`two_factor_required`** |

### O que a resposta explica

```json
{
  "key": "f348e41c-80b2-4f86-8e78-4b1a0ff3ce6a",
  "status": "two_factor_required",
  "metadata": {
    "risk_score": 50,
    "decision": {
      "reason": "risk_score",
      "rules": ["night_without_history", "high_amount_without_history", "new_destination", "new_destination_account"],
      "description": "2FA pedido: risco 50 de 100 (50 deste Pix + 0 herdado das últimas 24h). Pesaram: horário 20, valor 15, destino 15"
    },
    "risk": {
      "transaction_risk": 50,
      "inherited_risk": 0,
      "metrics": [ { "metric": "hour", "label": "horário", "points": 20, "max_points": 35, "rules": ["night_without_history"] }, "..." ]
    },
    "signals": [ { "rule": "night_without_history", "points": 20, "observed": { "local_time": "22:15" } }, "..." ],
    "receita": { "source": "api", "situation": "regular" },
    "arrival": { "received_at": "...", "far_from_arrival": false },
    "two_factor": { "result": "pending", "expires_at": "...", "resolved_at": null },
    "events": [ { "type": "analysis_created", "error_code": null, "details": { "status": "two_factor_required", "risk_score": 50, "decision_reason": "risk_score" }, "request_id": "...", "created_at": "..." } ]
  }
}
```

- `decision`: por que saiu este status, em uma frase. O `reason` diz quem decidiu: um fato (`blocking_rule`), a faixa do risco (`risk_score`) ou o Modo Rua (`two_factor_rule`).
- `risk.metrics`: quanto cada métrica pesou — inclusive as que ficaram em zero.
- `signals`: o que cada sinal viu.
- `receita`: de onde veio a situação de quem recebe (`api`, `cache`, `unavailable`, `skipped`, `disabled`).
- `arrival`: todas as janelas contam a partir da data do Pix (`transaction_date`). Se ela estiver a mais de 15 minutos da hora de chegada, fica marcado aqui — não muda a decisão, só deixa visível.
- `events`: a linha do tempo do Pix, em ordem (ver [A linha do tempo de cada Pix](#a-linha-do-tempo-de-cada-pix-pix_event)).

### Falta de dado nunca reprova

Se a Receita estiver lenta, fora do ar ou responder algo quebrado, o Pix segue com as outras métricas e a falha fica em `metadata.receita`. A consulta tem um prazo total de 0,5 s (DNS incluído): com a Receita fora do ar, o Pix responde em menos de um segundo.

### O que o motor não pega

Um Pix pequeno, no horário de costume, de casa, para uma conta antiga e conhecida passa como `approved` — mesmo que seja o ladrão. Pegar esse caso exigiria pedir 2FA para quase todo Pix, e travar o cliente bom também é prejuízo. A troca é consciente. Pelo mesmo motivo, o golpe que fraciona o valor só pesa quando a soma do dia passa de R$ 1.000.

### O relógio do motor: a data do Pix, e não a hora de chegada

Todas as janelas (horário, velocidade, herança, idade da conta) contam a partir do `transaction_date` que vem no payload, e não da hora em que o Pix chegou aqui (`received_at`).

**Por que assim:**
- Quem chama o antifraude é o banco, autenticado — não o cliente final. A data é a do Pix, informada por quem o processou.
- A decisão fica **reprodutível**: o mesmo payload dá a mesma decisão a qualquer hora. O exemplo do desafio (22:15) é noturno sempre, e não só se for testado à noite. Com `received_at`, o mesmo JSON daria 2FA às 23h e aprovação às 14h.

**O risco, assumido:** uma data muito distante da chegada pode fazer um Pix escapar das janelas de velocidade e herança. Por isso a distância é sempre medida e registrada em `metadata.arrival` (`far_from_arrival` passa de 15 minutos), e datas absurdas são recusadas (antes do lançamento do Pix ou depois de 2100).

**Em produção:** velocidade e herança passariam a contar pela chegada (`received_at`), que ninguém de fora controla, e um Pix com a data a mais de alguns minutos da chegada seria recusado na porta. O horário do Pix continuaria vindo do payload.

### Segurança

| Ponto | Aqui | Em produção |
|---|---|---|
| Segredos de exemplo | O token `default_token` e a senha `bootcamp` estão no repositório para o projeto subir sem configurar nada. **Fora de `APP_ENV=local`/`test`, a API se recusa a subir com eles** (`src/constants.py`) | Segredos de um cofre (secret manager), nunca no repositório |
| Autenticação | Um token compartilhado (`INTERNAL-TOKEN`), no padrão do Base Service | Identidade de serviço (mTLS ou JWT entre serviços) e permissões separadas: quem analisa (POST), quem consulta (GET), quem informa o 2FA |
| Volume de chamadas | Sem limite | Rate limit no gateway, na frente da API — e não dentro dela: com vários processos atendendo, um contador em memória não enxergaria os outros |
| Dados na resposta | **Minimizados:** o metadata não traz IP, nem identificador de aparelho, nem documento — só o que explica a decisão (contagens, distâncias, faixas) | Igual, e com o histórico apagado depois de 90 dias |

---

## Como rodar e testar

Precisa só de Docker.

```bash
docker compose up -d --build
```

Sobem três serviços: a API (porta 3000), o PostgreSQL e o mock da Receita Federal. A API responde em http://localhost:3000.

### Mandar um Pix

```bash
curl -X POST http://localhost:3000/pix/transaction \
  -H "INTERNAL-TOKEN: default_token" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "pix-000123",
    "amount": 150000,
    "transaction_date": "2026-10-17T22:15:00-03:00",
    "source_account": {
      "branch": "0001", "account_number": "88213",
      "opening_date": "2025-03-02T10:00:00-03:00",
      "owner": { "document_number": "123.456.789-09" }
    },
    "destination_account": {
      "participant": "60701190", "branch": "3675", "account_number": "10442",
      "opening_date": "2026-10-10T09:00:00-03:00",
      "owner": { "document_number": "987.654.321-00" }
    },
    "source": {
      "device_id": "dvc-9f2c71a8", "channel": "app", "ip": "198.51.100.23",
      "geolocation": { "latitude": -23.56, "longitude": -46.65 }
    }
  }'
```

### Consultar a análise

```bash
curl http://localhost:3000/pix/transaction/pix-000123 -H "INTERNAL-TOKEN: default_token"
```

### Informar o resultado do 2FA (até 3 minutos depois)

```bash
curl -X POST http://localhost:3000/pix/transaction/pix-000123/two-factor \
  -H "INTERNAL-TOKEN: default_token" \
  -H "Content-Type: application/json" \
  -d '{"result": "confirmed"}'
```

### Respostas

| Situação | HTTP | Código |
|---|---|---|
| Análise nova | 201 | |
| Reenvio do mesmo id, mesmo conteúdo | 200 | (a análise original) |
| Mesmo id, conteúdo diferente | 409 | `QIT002001` |
| JSON fora do contrato | 400 | `QIT000001` |
| Sem `INTERNAL-TOKEN` | 403 | `QIT000002` |
| Data que não existe, ou fora do período possível* | 422 | `QIT002005` |
| Pix não encontrado | 404 | `QIT002002` |
| 2FA de um Pix que não pediu 2FA | 409 | `QIT002003` |
| 2FA já respondido | 409 | `QIT002004` |
| 2FA respondido depois do prazo | 409 | `QIT002006` |

\* O `transaction_date` precisa estar entre 16/11/2020 (o lançamento do Pix) e 2100; as datas de abertura de conta, entre 1900 e 2100. O fuso precisa existir no mundo: de −12:00 a +14:00.

**O que a porta recusa, e por quê:**

- **`amount` só como inteiro JSON.** `5000.0` e `1E2` são recusados: o valor é em centavos, e `9.2e18` chegaria com perda de precisão.
- **Texto sem caractere de controle nem Unicode quebrado** (surrogate solto, como `\ud800`), em qualquer campo e em nome de campo: não cabem no banco nem numa resposta JSON.
- **`id` que volta no endereço do GET**: sem `/`, espaço ou acento, e nem `.` ou `..`, que os clientes HTTP resolvem como pasta.
- **Mensagens de erro curtas**: a descrição para em 500 caracteres. Um campo de 2 MB não volta inteiro na resposta.

### Rodar os testes

```bash
docker compose --profile tests run --build --rm tests
```

Os testes rodam num container próprio, contra a API de pé — sem Python na máquina.

Também dá para rodar com `pytest` direto na máquina (Linux, Python 3.11), com a API de pé (`docker compose up -d --build`):

```bash
python3 -m venv .venv && . .venv/bin/activate
```

```bash
pip install -r requirements-dev.txt && pytest
```

Os padrões já apontam para a API em `localhost:3000` e o banco em `localhost:5432`; não precisa de `.env`.

São dois grupos:

- `tests/integration/pix_transaction/`: os cenários pela API — contrato, fatos, cada métrica, Receita, Modo Rua, 2FA, herança e concorrência. Em `test_rule_edges.py`, cada limiar testado na borda (R$ 1.000 contra R$ 1.000,01, 29 contra 30 dias, nota 40 e nota 70), a mediana do ticket e a resposta que não expõe IP, aparelho nem documento. Em `test_pix_events.py`, a linha do tempo: cada evento, o `request_id` e a garantia de que ler não grava nada.
- `tests/integration/test_input_robustness.py`: entrada torta de todo tipo. Nenhuma pode virar 500:
  - **corpo:** o que não é JSON, JSON em UTF-16 ou com BOM, bytes que não são UTF-8, megabytes de espaço, 100 mil campos extras, corpo em chunks;
  - **campos:** tipos errados, números com 5.000 dígitos, NaN e infinito, inteiro escrito como `5000.0`, surrogate solto, caracteres de controle;
  - **datas:** datas impossíveis, fusos que não existem, conta aberta depois do Pix (reprovado e gravado);
  - **HTTP:** todo método em toda rota, content-types estranhos, caminhos com bytes inválidos, tokens e `X-Request-ID` estranhos;
  - **motor:** pontos antípodas e valores no limite do BIGINT somados.

**Testando à mão:** o motor tem memória. Pix do mesmo pagador em sequência contam na velocidade e herdam o risco dos anteriores — é o comportamento esperado, e o `metadata.risk` mostra de onde veio cada ponto. Para cenários independentes, troque o CPF do pagador (e o `device_id`, para a métrica de origem não somar).

### O mock da Receita

A resposta sai dos três primeiros dígitos do documento de quem recebe:

| Prefixo | Resposta |
|---|---|
| `000` | não existe (404) |
| `001` | titular falecido |
| `002` | cancelada |
| `003` | nula |
| `004` | suspensa |
| `005` | pendente de regularização |
| `006` | demora 3 s (simula lentidão) |
| `007` | erro 500 |
| `008` | resposta sem o campo `situation` |
| qualquer outro | regular |

### Configuração

| Variável | Padrão | Para quê |
|---|---|---|
| `APP_ENV` | `local` | Fora de `local`/`test`, a API não sobe com os valores de exemplo |
| `INTERNAL_TOKEN` | `default_token` | A senha que protege as rotas |
| `RECEITA_ENABLED` | `true` | `false` desliga a consulta à Receita; o motor roda igual, sem as regras dela |
| `RECEITA_API_TIMEOUT` | `0.5` | Prazo total da consulta, em segundos |
| `RECEITA_CACHE_HOURS` | `24` | Quanto tempo uma resposta da Receita vale |

### Mudou o `database.sql`?

O schema roda uma vez, quando o banco nasce. Para recriar:

```bash
docker compose down -v
```

```bash
docker compose up -d --build
```

### Lista restritiva

O CPF `529.982.247-25` já nasce na lista, para a demonstração. Para incluir outro documento (só dígitos):

```bash
docker compose exec db psql -U bootcamp -d bootcamp -c "INSERT INTO restricted_party (document_number, reason) VALUES ('12345678909', 'confirmed_fraud');"
```
