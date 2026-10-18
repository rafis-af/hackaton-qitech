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


-- As regras do motor. "Regra é dado": quanto cada sinal vale e se ele
-- está ligado mora aqui, e muda com um UPDATE, sem deploy. Os limiares
-- de cada regra (valores, janelas, contagens) moram no código, em
-- src/controllers/risk_rules.py, ao lado da conta que os usa.
--
-- action diz o que a regra faz quando dispara:
--   score       soma `points` ao score de risco do pagador
--   two_factor  força no mínimo two_factor_required (falta de dado, ou
--               uma condição que o próprio cliente ligou: o Modo Rua)
--   reject      reprova direto, sem passar pelo score
CREATE TABLE rule(
    id                              SERIAL PRIMARY KEY,
    enumerator                      VARCHAR(50) NOT NULL,
    family                          VARCHAR(20) NOT NULL,
    action                          VARCHAR(20) NOT NULL,
    points                          SMALLINT NOT NULL,
    active                          BOOLEAN NOT NULL DEFAULT(TRUE),
    description                     VARCHAR(255) NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(enumerator),
    CHECK(family IN ('integrity', 'limit', 'velocity', 'amount', 'profile', 'counterparty', 'authentication')),
    CHECK(action IN ('score', 'two_factor', 'reject')),
    -- Só regra de score vale ponto. Uma regra de bloqueio com ponto
    -- somaria duas vezes: reprovaria e ainda pesaria nos próximos Pix.
    CHECK((action = 'score' AND points > 0) OR (action <> 'score' AND points = 0))
);

INSERT INTO rule (enumerator, family, action, points, description) VALUES
-- Bloqueio direto
('invalid_document_number',      'integrity',      'reject',     0, 'CPF/CNPJ de quem paga ou de quem recebe com dígito verificador errado'),
('restricted_destination',       'counterparty',   'reject',     0, 'Quem recebe está na lista restritiva'),
('silver_daily_limit_exceeded',  'limit',          'reject',     0, 'Cliente Prata passaria do limite diário de R$ 1.000'),
-- Pede 2FA, nunca reprova: falta de dado ou condição ligada pelo cliente
('unknown_level_daily_limit',    'limit',          'two_factor', 0, 'Nível não informado e o dia passaria de R$ 1.000 (assume Prata)'),
('payer_without_history',        'profile',        'two_factor', 0, 'Pagador sem histórico fazendo Pix acima de R$ 200'),
('street_mode',                  'authentication', 'two_factor', 0, 'Modo Rua ativo e Pix acima do limite escolhido pelo cliente (ou sem limite)'),
-- Comportamento: soma pontos ao score
('night_limit',                  'limit',          'score',      2, 'Pix de pessoa física acima de R$ 1.000 entre 20h e 6h'),
('many_new_destinations',        'velocity',       'score',      2, '5 ou mais destinos novos em 24 horas'),
('split_below_night_limit',      'velocity',       'score',      2, '3 ou mais Pix noturnos entre R$ 800 e R$ 1.000 em 24 horas'),
('amount_above_profile',         'profile',        'score',      2, 'Valor acima de 3x o ticket médio, para um destino que o pagador nunca pagou'),
('device_changed',               'authentication', 'score',      1, 'Aparelho trocado: o pagador usou outro aparelho no último Pix ou nas últimas 24 horas'),
('device_shared',                'authentication', 'score',      2, 'O mesmo aparelho foi usado por outro pagador nos últimos 30 dias'),
('daily_sum_above_profile',      'amount',         'score',      1, 'Soma de 24 horas acima de 5x o ticket médio e acima de R$ 1.000'),
('new_destination_account',      'counterparty',   'score',      1, 'Conta de destino aberta há menos de 30 dias'),
('destination_many_payers',      'counterparty',   'score',      2, 'Conta de destino recebeu de 5 ou mais pagadores em 24 horas'),
('two_factor_failed_recently',   'authentication', 'score',      2, 'Pagador falhou no 2FA nas últimas 24 horas');


-- A lista restritiva: documentos com fraude confirmada ou sanção. É
-- dado local, consultado por índice, e não um serviço externo: a
-- decisão do Pix não pode esperar a rede.
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


-- ─────────────────────────────────────────────────────────────────
-- O Pix, como chegou
-- ─────────────────────────────────────────────────────────────────

-- Uma linha por Pix recebido, inclusive os reprovados: o histórico é
-- de TUDO que chegou, e uma tentativa barrada também é comportamento.
--
-- Os documentos ficam só com dígitos, para o mesmo CPF escrito com e
-- sem pontuação ser o mesmo pagador.
--
-- O Modo Rua chega pronto do app: quem sabe se o celular saiu das redes
-- Wi-Fi que o cliente cadastrou é o app, e o cadastro das redes é dele.
-- O antifraude só guarda o que recebeu, para a análise ser explicável.
CREATE TABLE pix_transaction(
    id                              SERIAL PRIMARY KEY,
    pix_id                          VARCHAR(100) NOT NULL,         -- o "id" do payload
    payload_hash                    CHAR(64) NOT NULL,             -- sha256 do payload, para o reenvio
    amount                          BIGINT NOT NULL,               -- centavos
    transaction_date                TIMESTAMPTZ NOT NULL,
    source_document                 VARCHAR(14) NOT NULL,
    source_level                    VARCHAR(10),                   -- opcional: silver | gold
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
    street_mode_active              BOOLEAN,                       -- opcional: NULL quando o app não informou
    street_mode_limit               BIGINT,                        -- centavos; NULL = todo Pix pede 2FA
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(pix_id),
    CHECK(amount > 0),
    CHECK(source_level IS NULL OR source_level IN ('silver', 'gold')),
    CHECK(street_mode_limit IS NULL OR street_mode_limit >= 0),
    CHECK(street_mode_limit IS NULL OR street_mode_active IS NOT NULL)
);

-- Os índices são o que deixa o motor rápido: toda regra de histórico
-- começa por "este pagador", "este destino" ou "este aparelho", numa
-- janela de tempo.
CREATE INDEX pix_transaction_source_date ON pix_transaction(source_document, transaction_date);
CREATE INDEX pix_transaction_source_dest ON pix_transaction(source_document, dest_participant, dest_branch, dest_account);
CREATE INDEX pix_transaction_dest_date ON pix_transaction(dest_participant, dest_branch, dest_account, transaction_date);
CREATE INDEX pix_transaction_device_date ON pix_transaction(device_id, transaction_date);


-- ─────────────────────────────────────────────────────────────────
-- A decisão
-- ─────────────────────────────────────────────────────────────────

-- Gravada uma vez e nunca alterada, inclusive quando o Pix é reprovado:
-- todo Pix que chega tem a sua análise, com o motivo da decisão.
--
-- O risk_score é uma fotografia: o score que o motor viu no momento. O
-- score que vale para o PRÓXIMO Pix é sempre recalculado a partir de
-- rule_result.
--
-- decision_reason diz POR QUE saiu este status, numa coluna e não só
-- dentro do JSON, para dar para perguntar ao banco "quantos Pix foram
-- barrados por regra de bloqueio hoje?":
--   blocking_rule    uma regra de bloqueio reprovou
--   risk_score       o score decidiu (aprovou, pediu 2FA ou reprovou)
--   two_factor_rule  o score aprovaria, mas uma regra de 2FA pediu
--                    confirmação (falta de dado ou Modo Rua)
-- As regras exatas que causaram a decisão ficam em metadata.decision.
CREATE TABLE analysis(
    id                              SERIAL PRIMARY KEY,
    analysis_key                    CHAR(36) NOT NULL,             -- o "key" da resposta
    pix_transaction_id              INTEGER NOT NULL REFERENCES pix_transaction(id),
    status_id                       INTEGER NOT NULL REFERENCES analysis_status(id),
    risk_score                      SMALLINT NOT NULL,
    decision_reason                 VARCHAR(20) NOT NULL,
    metadata                        JSONB NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(analysis_key),
    UNIQUE(pix_transaction_id),
    CHECK(risk_score BETWEEN 0 AND 5),
    CHECK(decision_reason IN ('blocking_rule', 'risk_score', 'two_factor_rule'))
);


-- Uma linha por regra que disparou. É daqui que sai o score: a soma
-- dos `points` do pagador nos últimos 7 dias.
--
-- `points` é COPIADO da regra no momento da decisão. Se amanhã um sinal
-- passar a valer 3, os Pix antigos continuam com o valor da época — é a
-- mesma ideia da tarifa com vigência da API Banco.
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

-- O que o cliente fez com o 2FA, informado pelo banco. Só existe linha
-- para Pix que pediram 2FA, e no máximo uma por análise. A regra "a
-- análise precisa estar em two_factor_required" mora no controller,
-- porque um CHECK não enxerga outra tabela.
CREATE TABLE two_factor_result(
    id                              SERIAL PRIMARY KEY,
    analysis_id                     INTEGER NOT NULL REFERENCES analysis(id),
    result                          VARCHAR(20) NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(analysis_id),
    CHECK(result IN ('confirmed', 'failed'))
);
