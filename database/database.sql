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

CREATE TABLE risk_metric(
    id                              SERIAL PRIMARY KEY,
    enumerator                      VARCHAR(30) NOT NULL,
    label                           VARCHAR(30) NOT NULL,
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

    CHECK(
        (action = 'score' AND risk_metric_id IS NOT NULL AND points > 0)
        OR (action = 'reject' AND risk_metric_id IS NULL AND points = 0)
    )
);

INSERT INTO rule (enumerator, risk_metric_id, action, points, description) VALUES

('invalid_document_number',       NULL, 'reject', 0, 'CPF/CNPJ de quem paga ou de quem recebe com dígito verificador errado'),
('restricted_payer',              NULL, 'reject', 0, 'Quem paga está na lista restritiva'),
('restricted_destination',        NULL, 'reject', 0, 'Quem recebe está na lista restritiva'),
('receita_destination_blocked',   NULL, 'reject', 0, 'Na Receita, o CPF/CNPJ de quem recebe não existe, foi cancelado, é nulo ou é de titular falecido'),

('unusual_hour',                  (SELECT id FROM risk_metric WHERE enumerator = 'hour'), 'score', 35, 'O cliente nunca fez Pix nesta faixa de horário nos últimos 90 dias'),
('rare_hour',                     (SELECT id FROM risk_metric WHERE enumerator = 'hour'), 'score', 20, 'Menos de 10% dos Pix do cliente saíram nesta faixa de horário'),
('night_without_history',         (SELECT id FROM risk_metric WHERE enumerator = 'hour'), 'score', 20, 'Pix entre 20h e 6h de um cliente ainda sem histórico de horários'),

('amount_5x_profile',             (SELECT id FROM risk_metric WHERE enumerator = 'amount'), 'score', 30, 'Valor 5x ou mais acima do ticket médio do cliente'),
('amount_3x_profile',             (SELECT id FROM risk_metric WHERE enumerator = 'amount'), 'score', 20, 'Valor entre 3x e 5x o ticket médio do cliente'),
('amount_2x_profile',             (SELECT id FROM risk_metric WHERE enumerator = 'amount'), 'score', 10, 'Valor entre 2x e 3x o ticket médio do cliente'),
('high_amount_without_history',   (SELECT id FROM risk_metric WHERE enumerator = 'amount'), 'score', 15, 'Pix acima de R$ 1.000 de um cliente ainda sem ticket médio'),

('far_from_usual_places',         (SELECT id FROM risk_metric WHERE enumerator = 'location'), 'score', 25, 'Modo Rua: Pix a mais de 30 km de todos os lugares de costume do cliente'),

('burst_4_in_10_minutes',         (SELECT id FROM risk_metric WHERE enumerator = 'velocity'), 'score', 20, '4 ou mais Pix do cliente em 10 minutos'),
('burst_3_in_10_minutes',         (SELECT id FROM risk_metric WHERE enumerator = 'velocity'), 'score', 10, '3 Pix do cliente em 10 minutos'),

('new_destination',               (SELECT id FROM risk_metric WHERE enumerator = 'destination'), 'score', 5,  'O cliente nunca pagou esta conta'),
('new_destination_account',       (SELECT id FROM risk_metric WHERE enumerator = 'destination'), 'score', 10, 'Conta de destino aberta há menos de 30 dias'),
('destination_many_payers',       (SELECT id FROM risk_metric WHERE enumerator = 'destination'), 'score', 10, 'Conta de destino recebeu de 5 ou mais pagadores em 24 horas'),
('receita_destination_irregular', (SELECT id FROM risk_metric WHERE enumerator = 'destination'), 'score', 10, 'Na Receita, o CPF/CNPJ de quem recebe está suspenso ou pendente de regularização'),

('device_changed',                (SELECT id FROM risk_metric WHERE enumerator = 'origin'), 'score', 5,  'O cliente trocou de aparelho no último Pix ou nas últimas 24 horas'),
('device_shared',                 (SELECT id FROM risk_metric WHERE enumerator = 'origin'), 'score', 10, 'O mesmo aparelho foi usado por outro pagador nos últimos 30 dias'),
('ip_shared',                     (SELECT id FROM risk_metric WHERE enumerator = 'origin'), 'score', 10, 'O mesmo IP foi usado por outro pagador nas últimas 24 horas');

CREATE TABLE restricted_party(
    id                              SERIAL PRIMARY KEY,
    document_number                 VARCHAR(14) NOT NULL,
    reason                          VARCHAR(50) NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(document_number),
    CHECK(reason IN ('confirmed_fraud', 'sanctions', 'court_order'))
);

INSERT INTO restricted_party (document_number, reason) VALUES
('52998224725', 'confirmed_fraud');

CREATE TABLE document_situation(
    id                              SERIAL PRIMARY KEY,
    document_number                 VARCHAR(14) NOT NULL,
    situation                       VARCHAR(30) NOT NULL,
    consulted_at                    TIMESTAMPTZ NOT NULL,
    UNIQUE(document_number),
    CHECK(situation IN ('regular', 'suspended', 'pending_regularization', 'canceled', 'deceased', 'nullified', 'not_found'))
);

CREATE TABLE pix_transaction(
    id                              SERIAL PRIMARY KEY,
    pix_id                          VARCHAR(100) NOT NULL,
    payload_hash                    CHAR(64) NOT NULL,
    amount                          BIGINT NOT NULL,
    transaction_date                TIMESTAMPTZ NOT NULL,
    source_document                 VARCHAR(14) NOT NULL,
    source_branch                   VARCHAR(10) NOT NULL,
    source_account                  VARCHAR(20) NOT NULL,
    source_opening_date             TIMESTAMPTZ NOT NULL,
    dest_participant                CHAR(8) NOT NULL,
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

CREATE INDEX pix_transaction_source_date ON pix_transaction(source_document, transaction_date);
CREATE INDEX pix_transaction_source_dest ON pix_transaction(source_document, dest_participant, dest_branch, dest_account);
CREATE INDEX pix_transaction_dest_date ON pix_transaction(dest_participant, dest_branch, dest_account, transaction_date);
CREATE INDEX pix_transaction_device_date ON pix_transaction(device_id, transaction_date);
CREATE INDEX pix_transaction_ip_date ON pix_transaction(ip, transaction_date);

CREATE TABLE analysis(
    id                              SERIAL PRIMARY KEY,
    analysis_key                    CHAR(36) NOT NULL,
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

CREATE TABLE rule_result(
    id                              SERIAL PRIMARY KEY,
    analysis_id                     INTEGER NOT NULL REFERENCES analysis(id),
    rule_id                         INTEGER NOT NULL REFERENCES rule(id),
    points                          SMALLINT NOT NULL,
    observed                        JSONB NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(analysis_id, rule_id),
    CHECK(points >= 0)
);

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

CREATE INDEX two_factor_result_pending ON two_factor_result(created_at) WHERE result = 'pending';
