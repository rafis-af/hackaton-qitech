-- ─────────────────────────────────────────────────────────────────
-- Antifraude Pix: o schema inteiro.
--
-- Este arquivo roda UMA vez, quando o banco nasce. Mudou alguma coisa
-- aqui? O banco só enxerga depois de nascer de novo:
--
--     docker compose down -v && docker compose up
--
-- O desenho, em uma frase: o Pix chega e é gravado como chegou
-- (pix_transaction), o motor decide (analysis), cada regra que disparou
-- vira uma linha (rule_result), e o que acontece DEPOIS da decisão vai
-- para outra tabela (two_factor_result). Nenhuma linha de analysis
-- recebe UPDATE: a decisão do motor fica gravada exatamente como foi
-- tomada.
--
-- O payload é o do desafio, sem nenhum campo a mais. Tudo o que o motor
-- sabe vem dele, do histórico gravado aqui e da Receita Federal (o único
-- serviço de fora, consultado sobre quem recebe).
--
-- Retenção (LGPD): aqui o histórico fica para sempre, porque é um
-- projeto de estudo. Em produção, o que passa de 90 dias sai do banco:
-- é a maior janela que alguma regra usa (o perfil e os lugares de
-- costume do pagador), e guardar IP, aparelho e localização além disso
-- não serve a regra nenhuma.
-- ─────────────────────────────────────────────────────────────────


-- ─────────────────────────────────────────────────────────────────
-- Catálogos
-- ─────────────────────────────────────────────────────────────────

-- As três decisões possíveis. É um catálogo, no padrão do Base Service,
-- e não um texto solto em analysis: um status com erro de digitação
-- nunca chega ao banco, porque a chave estrangeira recusa.
CREATE TABLE analysis_status(
    id                              SERIAL PRIMARY KEY,
    enumerator                      VARCHAR(50) NOT NULL,
    description                     VARCHAR(255) NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(enumerator)
);

INSERT INTO analysis_status (enumerator, description) VALUES
('approved', 'Segue normal, sem fricção para o cliente'),
('two_factor_required', 'Há dúvida: o banco pede o segundo fator ao cliente'),
('rejected', 'O risco é claro: o Pix não sai');


-- As métricas do risco: O QUE o motor mede em cada Pix, e quanto cada
-- uma pode pesar no máximo. O risco de um Pix vai de 0 a 100 e é a soma
-- das métricas.
--
-- A regra mais importante do motor mora no CHECK desta tabela: nenhuma
-- métrica pode valer 40 ou mais, e 40 é onde começa o 2FA. Por isso
-- NENHUMA MÉTRICA DECIDE SOZINHA — não é uma intenção escrita no README,
-- é uma conta que o banco não deixa quebrar. Duas métricas fortes pedem
-- 2FA; reprovar (70) exige pelo menos três.
--
-- A ordem dos pesos é a tese: o horário (35) pesa mais que o lugar (25).
-- Todas comparam o Pix com o histórico do PRÓPRIO cliente: a pergunta
-- não é "isso é estranho?", é "isso é estranho para esta pessoa?".
CREATE TABLE risk_metric(
    id                              SERIAL PRIMARY KEY,
    enumerator                      VARCHAR(30) NOT NULL,
    label                           VARCHAR(30) NOT NULL,          -- o nome que aparece na resposta
    description                     VARCHAR(255) NOT NULL,
    max_points                      SMALLINT NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(enumerator),
    CHECK(max_points > 0 AND max_points < 40)
);

INSERT INTO risk_metric (enumerator, label, description, max_points) VALUES
('hour',        'horário',     'O Pix saiu num horário em que o cliente não costuma pagar', 35),
('amount',      'valor',       'O valor está acima do ticket médio do cliente', 30),
('location',    'local',       'O Pix saiu longe de todos os lugares de costume do cliente (Modo Rua)', 25),
('velocity',    'velocidade',  'Muitos Pix do cliente em poucos minutos', 20),
('destination', 'destino',     'Quem recebe é novo para o cliente, ou dá sinais de conta laranja', 15),
('origin',      'origem',      'O aparelho ou o IP não batem com a premissa de um aparelho por conta', 10);


-- Os sinais do motor: O QUE o motor viu, e quanto vale. "Regra é dado":
-- os pontos e o liga/desliga moram aqui, e mudam com um UPDATE, sem
-- deploy. Os limiares (3x o ticket, 30 km, 10 minutos) moram no código,
-- em src/controllers/risk_rules.py, ao lado da conta que os usa.
--
-- Dois tipos:
--   score   um sinal de COMPORTAMENTO, ligado a uma métrica. Os sinais
--           de uma mesma métrica somam, mas nunca passam do teto dela
--           (risk_metric.max_points).
--   reject  um FATO que reprova direto, sem métrica: documento que não
--           existe, lista restritiva, CPF de quem recebe cancelado ou de
--           titular falecido. Nenhuma métrica de comportamento decide
--           sozinha; fatos objetivos, sim.
CREATE TABLE rule(
    id                              SERIAL PRIMARY KEY,
    enumerator                      VARCHAR(50) NOT NULL,
    risk_metric_id                  INTEGER REFERENCES risk_metric(id),
    action                          VARCHAR(20) NOT NULL,
    points                          SMALLINT NOT NULL,
    active                          BOOLEAN NOT NULL DEFAULT(TRUE),
    description                     VARCHAR(255) NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(enumerator),
    CHECK(action IN ('score', 'reject')),
    -- Sinal de comportamento pertence a uma métrica e vale ponto; fato
    -- que reprova não pertence a métrica nenhuma e não vale ponto.
    CHECK(
        (action = 'score' AND risk_metric_id IS NOT NULL AND points > 0)
        OR (action = 'reject' AND risk_metric_id IS NULL AND points = 0)
    )
);

INSERT INTO rule (enumerator, risk_metric_id, action, points, description) VALUES
-- Fatos: reprovam direto
('invalid_document_number',       NULL, 'reject', 0, 'CPF/CNPJ de quem paga ou de quem recebe com dígito verificador errado'),
('restricted_payer',              NULL, 'reject', 0, 'Quem paga está na lista restritiva'),
('restricted_destination',        NULL, 'reject', 0, 'Quem recebe está na lista restritiva'),
('receita_destination_blocked',   NULL, 'reject', 0, 'Na Receita, o CPF/CNPJ de quem recebe não existe, foi cancelado, é nulo ou é de titular falecido'),
-- Horário (até 35)
('unusual_hour',                  (SELECT id FROM risk_metric WHERE enumerator = 'hour'), 'score', 35, 'O cliente nunca fez Pix nesta faixa de horário nos últimos 90 dias'),
('rare_hour',                     (SELECT id FROM risk_metric WHERE enumerator = 'hour'), 'score', 20, 'Menos de 10% dos Pix do cliente saíram nesta faixa de horário'),
('night_without_history',         (SELECT id FROM risk_metric WHERE enumerator = 'hour'), 'score', 20, 'Pix entre 20h e 6h de um cliente ainda sem histórico de horários'),
-- Valor (até 30)
('amount_5x_profile',             (SELECT id FROM risk_metric WHERE enumerator = 'amount'), 'score', 30, 'Valor 5x ou mais acima do ticket médio do cliente'),
('amount_3x_profile',             (SELECT id FROM risk_metric WHERE enumerator = 'amount'), 'score', 20, 'Valor entre 3x e 5x o ticket médio do cliente'),
('amount_2x_profile',             (SELECT id FROM risk_metric WHERE enumerator = 'amount'), 'score', 10, 'Valor entre 2x e 3x o ticket médio do cliente'),
('high_amount_without_history',   (SELECT id FROM risk_metric WHERE enumerator = 'amount'), 'score', 15, 'Pix acima de R$ 1.000 de um cliente ainda sem ticket médio'),
-- Local (até 25)
('far_from_usual_places',         (SELECT id FROM risk_metric WHERE enumerator = 'location'), 'score', 25, 'Modo Rua: Pix a mais de 30 km de todos os lugares de costume do cliente'),
-- Velocidade (até 20)
('burst_4_in_10_minutes',         (SELECT id FROM risk_metric WHERE enumerator = 'velocity'), 'score', 20, '4 ou mais Pix do cliente em 10 minutos'),
('burst_3_in_10_minutes',         (SELECT id FROM risk_metric WHERE enumerator = 'velocity'), 'score', 10, '3 Pix do cliente em 10 minutos'),
-- Destino (até 15)
('new_destination',               (SELECT id FROM risk_metric WHERE enumerator = 'destination'), 'score', 5,  'O cliente nunca pagou esta conta'),
('new_destination_account',       (SELECT id FROM risk_metric WHERE enumerator = 'destination'), 'score', 10, 'Conta de destino aberta há menos de 30 dias'),
('destination_many_payers',       (SELECT id FROM risk_metric WHERE enumerator = 'destination'), 'score', 10, 'Conta de destino recebeu de 5 ou mais pagadores em 24 horas'),
('receita_destination_irregular', (SELECT id FROM risk_metric WHERE enumerator = 'destination'), 'score', 10, 'Na Receita, o CPF/CNPJ de quem recebe está suspenso ou pendente de regularização'),
-- Origem (até 10)
('device_changed',                (SELECT id FROM risk_metric WHERE enumerator = 'origin'), 'score', 5,  'O cliente trocou de aparelho no último Pix ou nas últimas 24 horas'),
('device_shared',                 (SELECT id FROM risk_metric WHERE enumerator = 'origin'), 'score', 10, 'O mesmo aparelho foi usado por outro pagador nos últimos 30 dias'),
('ip_shared',                     (SELECT id FROM risk_metric WHERE enumerator = 'origin'), 'score', 10, 'O mesmo IP foi usado por outro pagador nas últimas 24 horas');


-- A lista restritiva: documentos com fraude confirmada ou sanção. É
-- dado local, consultado por índice, e não um serviço externo: a
-- decisão do Pix não pode esperar a rede. Vale para os dois lados do
-- Pix, quem paga e quem recebe.
CREATE TABLE restricted_party(
    id                              SERIAL PRIMARY KEY,
    document_number                 VARCHAR(14) NOT NULL,          -- só dígitos
    reason                          VARCHAR(50) NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(document_number),
    CHECK(reason IN ('confirmed_fraud', 'sanctions', 'court_order'))
);

-- Um documento de exemplo, para a demonstração e para os testes. É um
-- CPF válido na conta dos dígitos, gerado para teste, de ninguém.
INSERT INTO restricted_party (document_number, reason) VALUES
('52998224725', 'confirmed_fraud');


-- O que a Receita Federal respondeu sobre cada documento, guardado por
-- um tempo. A situação cadastral de um CPF muda raramente; consultar a
-- Receita a cada Pix para o mesmo destino seria latência e dependência
-- de rede à toa. Quem decide quanto tempo a resposta vale é o
-- controller (RECEITA_CACHE_HOURS).
--
-- Só resposta de verdade entra aqui. Timeout e erro da Receita não são
-- guardados: na próxima vez, ela é consultada de novo.
CREATE TABLE document_situation(
    id                              SERIAL PRIMARY KEY,
    document_number                 VARCHAR(14) NOT NULL,          -- só dígitos
    situation                       VARCHAR(30) NOT NULL,
    consulted_at                    TIMESTAMPTZ NOT NULL,
    UNIQUE(document_number),
    CHECK(situation IN ('regular', 'suspended', 'pending_regularization', 'canceled', 'deceased', 'nullified', 'not_found'))
);


-- ─────────────────────────────────────────────────────────────────
-- O Pix, como chegou
-- ─────────────────────────────────────────────────────────────────

-- Uma linha por Pix recebido, inclusive os reprovados: o histórico é
-- de TUDO que chegou, e uma tentativa barrada também é comportamento.
--
-- Os documentos ficam só com dígitos, para o mesmo CPF escrito com e
-- sem pontuação ser o mesmo pagador. A geolocalização é opcional no
-- contrato; quando vem, é ela que ensina ao Modo Rua os lugares de
-- costume do pagador.
CREATE TABLE pix_transaction(
    id                              SERIAL PRIMARY KEY,
    pix_id                          VARCHAR(100) NOT NULL,         -- o "id" do payload
    payload_hash                    CHAR(64) NOT NULL,             -- sha256 do payload, para o reenvio
    amount                          BIGINT NOT NULL,               -- centavos
    transaction_date                TIMESTAMPTZ NOT NULL,
    source_document                 VARCHAR(14) NOT NULL,
    source_branch                   VARCHAR(10) NOT NULL,
    source_account                  VARCHAR(20) NOT NULL,
    source_opening_date             TIMESTAMPTZ NOT NULL,
    dest_participant                CHAR(8) NOT NULL,              -- ISPB
    dest_branch                     VARCHAR(10) NOT NULL,
    dest_account                    VARCHAR(20) NOT NULL,
    dest_opening_date               TIMESTAMPTZ NOT NULL,
    dest_document                   VARCHAR(14) NOT NULL,
    device_id                       VARCHAR(100) NOT NULL,
    channel                         VARCHAR(20) NOT NULL,
    ip                              VARCHAR(45) NOT NULL,
    latitude                        NUMERIC(9, 6),
    longitude                       NUMERIC(9, 6),
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(pix_id),
    CHECK(amount > 0),
    CHECK((latitude IS NULL) = (longitude IS NULL))
);

-- Os índices são o que deixa o motor rápido: toda métrica começa por
-- "este pagador", "este destino", "este aparelho" ou "este IP", numa
-- janela de tempo.
CREATE INDEX pix_transaction_source_date ON pix_transaction(source_document, transaction_date);
CREATE INDEX pix_transaction_source_dest ON pix_transaction(source_document, dest_participant, dest_branch, dest_account);
CREATE INDEX pix_transaction_dest_date ON pix_transaction(dest_participant, dest_branch, dest_account, transaction_date);
CREATE INDEX pix_transaction_device_date ON pix_transaction(device_id, transaction_date);
CREATE INDEX pix_transaction_ip_date ON pix_transaction(ip, transaction_date);


-- ─────────────────────────────────────────────────────────────────
-- A decisão
-- ─────────────────────────────────────────────────────────────────

-- Gravada uma vez e nunca alterada, inclusive quando o Pix é reprovado:
-- todo Pix que chega tem a sua análise, com o motivo da decisão.
--
-- O risco tem três colunas, porque ele tem duas origens:
--   transaction_risk  o risco DESTE Pix, a soma das métricas (0 a 100)
--   inherited_risk    o que ele herdou dos Pix anteriores do mesmo
--                     cliente nas últimas 24 horas — o risco tem prazo
--                     de validade (veja src/controllers/risk_rules.py)
--   risk_score        o que decidiu: a soma dos dois, com teto de 100
-- A herança sai SEMPRE do transaction_risk dos anteriores, nunca do
-- risk_score: senão o risco herdado seria herdado de novo, Pix após Pix,
-- e cresceria sozinho.
--
-- decision_reason diz POR QUE saiu este status, numa coluna e não só
-- dentro do JSON, para dar para perguntar ao banco "quantos Pix foram
-- barrados por regra de bloqueio hoje?":
--   blocking_rule    um fato reprovou direto
--   risk_score       o risco decidiu (aprovou, pediu 2FA ou reprovou)
-- Os sinais exatos que causaram a decisão ficam em metadata.decision.
CREATE TABLE analysis(
    id                              SERIAL PRIMARY KEY,
    analysis_key                    CHAR(36) NOT NULL,             -- o "key" da resposta
    pix_transaction_id              INTEGER NOT NULL REFERENCES pix_transaction(id),
    status_id                       INTEGER NOT NULL REFERENCES analysis_status(id),
    transaction_risk                SMALLINT NOT NULL,
    inherited_risk                  SMALLINT NOT NULL,
    risk_score                      SMALLINT NOT NULL,
    decision_reason                 VARCHAR(20) NOT NULL,
    metadata                        JSONB NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(analysis_key),
    UNIQUE(pix_transaction_id),
    CHECK(transaction_risk BETWEEN 0 AND 100),
    CHECK(inherited_risk BETWEEN 0 AND 100),
    CHECK(risk_score BETWEEN 0 AND 100),
    CHECK(risk_score = LEAST(100, transaction_risk + inherited_risk)),
    CHECK(decision_reason IN ('blocking_rule', 'risk_score'))
);


-- Uma linha por sinal que disparou: o que o motor viu em cada Pix, e
-- quanto valeu. É o "motivo explicável" guardado em tabela, e não só no
-- JSON da resposta.
--
-- `points` é COPIADO do sinal no momento da decisão. Se amanhã um sinal
-- passar a valer outro tanto, os Pix antigos continuam com o valor da
-- época — é a mesma ideia da tarifa com vigência da API Banco.
CREATE TABLE rule_result(
    id                              SERIAL PRIMARY KEY,
    analysis_id                     INTEGER NOT NULL REFERENCES analysis(id),
    rule_id                         INTEGER NOT NULL REFERENCES rule(id),
    points                          SMALLINT NOT NULL,
    observed                        JSONB NOT NULL,                -- o que a regra viu, ex.: {"value": 6, "threshold": 5}
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(analysis_id, rule_id),
    CHECK(points >= 0)
);


-- ─────────────────────────────────────────────────────────────────
-- Depois da decisão
-- ─────────────────────────────────────────────────────────────────

-- A pendência do 2FA, do pedido até a resposta do cliente. Só existe
-- linha para Pix que pediram 2FA, e no máximo uma por análise.
--
-- A linha nasce JUNTO com a análise, em 'pending', com prazo para ser
-- respondida (expires_at), e muda uma vez só:
--   'confirmed' / 'failed'  o banco informou o resultado dentro do prazo
--   'expired'               a resposta chegou depois do prazo: o Pix
--                           já não pode mais sair, e a resposta é recusada
-- Assim a pendência é um dado, e não a ausência de um: dá para perguntar
-- ao banco "quais 2FA estão esperando o cliente?".
--
-- O prazo é gravado em cada linha, e não só calculado a partir de uma
-- constante: se o tempo limite mudar amanhã, os 2FA de hoje continuam
-- com o prazo que tinham quando foram pedidos.
--
-- O estado mora numa coluna só (result), e não num booleano "pendente"
-- ao lado de um resultado: com duas colunas, nada impediria uma linha
-- pendente E confirmada ao mesmo tempo. O CHECK de baixo amarra o
-- resolved_at ao estado: preenchido se e só se a pendência fechou.
--
-- A análise continua sem UPDATE. Quem muda é esta tabela, que guarda o
-- que aconteceu DEPOIS da decisão. A regra "a análise precisa estar em
-- two_factor_required" mora no controller, porque um CHECK não enxerga
-- outra tabela.
CREATE TABLE two_factor_result(
    id                              SERIAL PRIMARY KEY,
    analysis_id                     INTEGER NOT NULL REFERENCES analysis(id),
    result                          VARCHAR(20) NOT NULL DEFAULT('pending'),
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    expires_at                      TIMESTAMPTZ NOT NULL,
    resolved_at                     TIMESTAMPTZ,
    UNIQUE(analysis_id),
    CHECK(result IN ('pending', 'confirmed', 'failed', 'expired')),
    CHECK((result = 'pending') = (resolved_at IS NULL)),
    CHECK(expires_at > created_at)
);

-- "Quais 2FA estão pendentes?" sem varrer a tabela inteira.
CREATE INDEX two_factor_result_pending ON two_factor_result(created_at) WHERE result = 'pending';
