# [Nome do grupo]
**Integrantes:** Rafael Almeida Ferreira e Guilherme Silva Xavier

Motor antifraude de Pix do Hackathon QI Tech 2026. A API recebe um Pix, compara com o histórico daquele cliente e daquele destino, e devolve uma decisão — `approved`, `two_factor_required` ou `rejected` — com o motivo de cada ponto de risco.

Ela não move dinheiro: quem envia ou cancela o Pix é o banco. O antifraude decide e explica.

---

## Como o motor de decisão funciona

### A tese

**Cada Pix é comparado com o histórico do próprio cliente.** A pergunta não é "isso é estranho?", é "isso é estranho *para esta pessoa*?". Um Pix às 3h da manhã é normal para quem trabalha de madrugada e estranho para quem sempre pagou de dia; R$ 5.000 é o aluguel de um cliente e dez vezes o ticket de outro.

Daí saem os três princípios que organizam o motor:

1. **Nenhuma métrica decide sozinha.** O risco é a soma de seis métricas, e a maior delas vale 35 — abaixo dos 40 em que começa o 2FA. Isso não é só uma intenção: é um `CHECK` na tabela `risk_metric`.
2. **O horário pesa mais que o lugar.** O celular roubado é usado quando o dono não usaria; isso diz mais do que de onde ele é usado.
3. **O risco tem prazo de validade.** Um Pix arriscado deixa parte do risco para os Pix seguintes do mesmo cliente por 24 horas — e depois vence.

E uma exceção, dita em voz alta: **fatos não são métricas.** Documento que não existe, lista restritiva e CPF de pessoa falecida na Receita reprovam direto. Nenhuma métrica de *comportamento* decide sozinha; fatos objetivos, sim.

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
  └─ 9. grava tudo                  o Pix, a decisão, cada sinal — inclusive quando reprova
```

O motor (`src/controllers/risk_rules.py`) é feito de **funções puras**: o controller busca os números no banco, e cada sinal só compara. Por isso cada regra é testada sem banco, e a mesma entrada dá sempre a mesma decisão.

Os **pontos de cada sinal** e o **teto de cada métrica** moram no banco (tabelas `rule` e `risk_metric`): mudam com um `UPDATE`, sem deploy. Os **limiares** (3x o ticket, 30 km, 10 minutos) moram no código, ao lado da conta que os usa.

### O que é "normal" para o cliente

Só **Pix confiáveis** ensinam o normal: aprovados direto, ou com 2FA confirmado. É com eles que o motor aprende o horário de costume, o ticket médio, os lugares de costume e os destinos conhecidos. O que o ladrão fez — reprovado, 2FA pendente ou falhado — nunca vira o normal de ninguém.

Já as **tentativas** contam todas, inclusive as reprovadas: a rajada do ladrão conta mesmo quando foi barrada.

### Modelagem de dados

```
analysis_status ─┐                    risk_metric ──┐
                 ▼                                  ▼
pix_transaction ─1:1─► analysis ─1:N─► rule_result ◄─N:1─ rule
                         │
                         └─1:0..1─► two_factor_result

restricted_party      (lista restritiva, consultada por documento)
document_situation    (cache das respostas da Receita)
```

| Tabela | O que guarda | Decisões |
|---|---|---|
| `pix_transaction` | O Pix como chegou | Documentos só com dígitos (o mesmo CPF com e sem máscara é o mesmo pagador). `pix_id` único. Hash do payload para reconhecer o reenvio |
| `analysis` | A decisão | **Nunca recebe UPDATE.** Guarda `transaction_risk`, `inherited_risk` e `risk_score` — e um `CHECK` garante que o terceiro é a soma dos dois, com teto de 100. `decision_reason` permite perguntar ao banco "quantos foram barrados por fato hoje?" |
| `rule_result` | Cada sinal que disparou, com o que viu | `points` é **copiado** do sinal no momento: se um peso mudar amanhã, as análises antigas continuam com o valor da época |
| `risk_metric` | As 6 métricas e o teto de cada uma | `CHECK (max_points < 40)`: nenhuma métrica decide sozinha |
| `rule` | Os sinais: pontos, liga/desliga, métrica | Sinal de comportamento pertence a uma métrica e vale ponto; fato não tem métrica e não vale ponto (`CHECK`) |
| `two_factor_result` | A pendência do 2FA | Nasce `pending` com a análise, com prazo (`expires_at`), e fecha uma vez: `confirmed`, `failed` ou `expired`. Estado numa coluna só, para não existir "pendente e confirmado" ao mesmo tempo |
| `analysis_status` | Os 3 status | Catálogo com chave estrangeira, no padrão do Base Service |
| `restricted_party` | Documentos com fraude confirmada, sanção ou ordem judicial | Local, consultado por índice: a decisão não espera a rede |
| `document_situation` | O que a Receita respondeu sobre cada documento | Cache de 24 h. Só resposta de verdade entra; falha da Receita não é guardada |

Cinco índices em `pix_transaction` cobrem as consultas do motor: toda métrica começa por "este pagador", "este destino", "este aparelho" ou "este IP", numa janela de tempo.

**Retenção (LGPD):** aqui o histórico fica para sempre, por ser um projeto de estudo. Em produção, o que passa de 90 dias sairia do banco — é a maior janela que o motor usa, e guardar IP, aparelho e localização além disso não serve a regra nenhuma.

---

## Regras

### As métricas

| Métrica | Teto | O que pergunta |
|---|---|---|
| Horário | 35 | O cliente costuma pagar nesta hora? |
| Valor | 30 | O valor cabe no ticket médio dele? |
| Local (Modo Rua) | 25 | Saiu de perto dos lugares de costume? |
| Velocidade | 20 | Quantos Pix ele fez nos últimos minutos? |
| Destino | 15 | Ele já pagou esta conta? Ela parece laranja? |
| Origem | 10 | O aparelho e o IP batem com a conta? |

Os sinais de uma mesma métrica somam, mas nunca passam do teto dela.

### Os sinais

| Regra | Lógica | Parâmetros | Por quê |
|---|---|---|---|
| **Horário** | | | |
| `unusual_hour` (35) | O cliente nunca fez Pix nesta faixa de horário | Faixa = a hora do Pix ±1h; janela de 90 dias; mínimo de 5 Pix confiáveis | O celular roubado é usado quando o dono não usaria. ±1h para quem paga às 14h não ficar "fora do padrão" às 15h |
| `rare_hour` (20) | Menos de 10% dos Pix do cliente nesta faixa | 10% | Horário que acontece, mas é exceção |
| `night_without_history` (20) | Cliente sem histórico de horários, Pix entre 20h e 6h | 20h–6h; menos de 5 Pix confiáveis | Sem histórico, vale a referência de todo mundo: o período noturno do Banco Central (Resolução BCB nº 142/2021) |
| **Valor** | | | |
| `amount_5x_profile` (30) | Valor ≥ 5x o ticket médio | Mínimo de 3 Pix confiáveis | Com menos de 3 Pix, a média não diz nada |
| `amount_3x_profile` (20) | Valor entre 3x e 5x o ticket médio | | Degraus, para que "um pouco acima" pese menos que "muito acima" |
| `amount_2x_profile` (10) | Valor entre 2x e 3x o ticket médio | | |
| `high_amount_without_history` (15) | Cliente sem ticket médio, Pix acima de R$ 1.000 | R$ 1.000 | Sem histórico, o limite noturno do Banco Central serve de referência de "valor alto" |
| **Local** | | | |
| `far_from_usual_places` (25) | Pix a mais de 30 km de **todos** os lugares de costume | 30 km; lugares de Pix confiáveis em 90 dias | 30 km cobre uma região metropolitana. A distância é até o lugar **mais perto**, e não até a média: quem mora em São Paulo e trabalha em Campinas teria a média no meio da estrada. Sem geolocalização (é opcional) ou sem lugar de costume, não roda |
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
| `restricted_payer` | Quem paga está na lista restritiva | Fraude confirmada, sanção ou ordem judicial |
| `restricted_destination` | Quem recebe está na lista restritiva | Idem |
| `receita_destination_blocked` | Na Receita, o documento de quem recebe não existe, foi cancelado, é nulo ou é de titular falecido | Ninguém legítimo recebe Pix num CPF assim: é o documento clássico de conta laranja |

---

## Como as regras viram decisão

### A ordem

```
1. Algum fato disparou?                  → rejected
2. risco = risco deste Pix + risco herdado, com teto de 100
       0 a 39   → approved
      40 a 69   → two_factor_required
      70 a 100  → rejected
```

Duas métricas fortes pedem 2FA; reprovar exige três. Exemplos:

| Situação | Conta | Decisão |
|---|---|---|
| Aluguel de R$ 5.000, destino de sempre, horário de sempre | valor 30 | `approved` |
| Viagem: Pix de dia, em outra cidade | local 25 + destino novo 5 | `approved` |
| Viagem: Pix à noite, em outra cidade, cliente sem histórico de horários | horário 20 + local 25 + destino 5 | `two_factor_required` |
| Valor alto para conta aberta há 5 dias | valor 30 + destino 15 | `two_factor_required` |
| Às 3h (nunca pagou nesse horário), de outra cidade, valor 6x o ticket | horário 35 + local 25 + valor 30 | `rejected` |

### Quando o motor pede 2FA

Quando o risco fica entre 40 e 69: há dúvida, e o cliente confirma que é ele mesmo. Quem pede o segundo fator (a selfie, a biometria) é o banco; o antifraude só decide quando pedir.

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
| Valor | 15 | Acima de R$ 1.000, cliente sem ticket médio |
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
    "two_factor": { "result": "pending", "expires_at": "...", "resolved_at": null }
  }
}
```

- `decision`: por que saiu este status, em uma frase.
- `risk.metrics`: quanto cada métrica pesou — inclusive as que ficaram em zero.
- `signals`: o que cada sinal viu.
- `receita`: de onde veio a situação de quem recebe (`api`, `cache`, `unavailable`, `skipped`, `disabled`).
- `arrival`: todas as janelas contam a partir da data do Pix (`transaction_date`). Se ela estiver a mais de 15 minutos da hora de chegada, fica marcado aqui — não muda a decisão, só deixa visível.

### Falta de dado nunca reprova

Se a Receita estiver lenta, fora do ar ou responder algo quebrado, o Pix segue com as outras métricas e a falha fica em `metadata.receita`. A consulta tem um prazo total de 0,5 s (DNS incluído): com a Receita fora do ar, o Pix responde em menos de um segundo.

### O que o motor não pega

Um Pix pequeno, no horário de costume, de casa, para uma conta antiga e conhecida passa como `approved` — mesmo que seja o ladrão. Pegar esse caso exigiria pedir 2FA para quase todo Pix, e travar o cliente bom também é prejuízo. A troca é consciente.

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

\* O `transaction_date` precisa estar entre 16/11/2020 (o lançamento do Pix) e 2100; as datas de abertura de conta, entre 1900 e 2100.

### Rodar os testes

```bash
docker compose --profile tests run --build --rm tests
```

Os testes rodam num container próprio, contra a API de pé — sem Python na máquina. São três grupos:

- `tests/unit/`: o motor, sem banco — cada limiar testado na borda, as faixas, a herança, e as propriedades da tese (nenhuma métrica decide sozinha, horário pesa mais que local).
- `tests/integration/pix_transaction/`: os cenários pela API — contrato, fatos, cada métrica, Receita, 2FA, herança e concorrência.
- `tests/integration/test_input_robustness.py`: entrada torta de todo tipo (corpo que não é JSON, tipos errados, datas impossíveis, NaN, caracteres de controle, ids que não cabem num endereço). Nenhuma pode virar 500.

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
