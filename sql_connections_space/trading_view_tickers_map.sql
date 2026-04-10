-- -- @block
-- CREATE TABLE IF NOT EXISTS trading_view_company_data_map (
--     internal_id INT PRIMARY KEY AUTO_INCREMENT,
--     symbol VARCHAR(50) NOT NULL,
--     name VARCHAR(128),
--     exchange VARCHAR(32),
--     description VARCHAR(256),
--     type VARCHAR(32),
--     typespecs VARCHAR(128),
--     market_cap_basic BIGINT,
--     fundamental_currency_code VARCHAR(8),
--     market VARCHAR(32),
--     kind VARCHAR(32),
--     change_pct DOUBLE,
--     perf_5y DOUBLE,
--     perf_6m DOUBLE,
--     perf_all DOUBLE,
--     perf_1m DOUBLE,
--     perf_w DOUBLE,
--     perf_y DOUBLE,
--     perf_ytd DOUBLE,
--     perf_10y DOUBLE,
--     perf_3y DOUBLE,
--     perf_5d DOUBLE,
--     logoid VARCHAR(64),
--     logo_style VARCHAR(32),
--     kind_delay INT,
--     updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
--     UNIQUE KEY uq_symbol (symbol)
-- );
-- -- @block
-- CREATE INDEX idx_symbol ON trading_view_company_data_map(symbol);
-- CREATE INDEX idx_name ON trading_view_company_data_map(name);
-- CREATE INDEX idx_market ON trading_view_company_data_map(market);
-- CREATE INDEX idx_exchange ON trading_view_company_data_map(exchange);
-- @block
SELECT *
FROM trading_view_company_data_map;
-- ────────────────────────────────────────────────────────────────────────
-- Portfolio management tables
-- ────────────────────────────────────────────────────────────────────────
-- @block  Portfolios registry
CREATE TABLE IF NOT EXISTS portfolio_registry (
    portfolio_id INT PRIMARY KEY AUTO_INCREMENT,
    portfolio_name VARCHAR(128) NOT NULL,
    description VARCHAR(512),
    currency VARCHAR(8) DEFAULT 'USD',
    benchmark_symbol VARCHAR(32),
    notes VARCHAR(1024),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_portfolio_name (portfolio_name)
);
-- @block  Persistent per-portfolio position state
CREATE TABLE IF NOT EXISTS portfolio_managements_data_v1 (
    portfolio_item_id INT PRIMARY KEY AUTO_INCREMENT,
    portfolio_id INT NOT NULL,
    company_internal_id INT NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'ACTIVE',
    shares_held DOUBLE NOT NULL DEFAULT 0,
    average_entry_price DOUBLE NOT NULL DEFAULT 0,
    cost_basis_total DOUBLE NOT NULL DEFAULT 0,
    realized_pnl DOUBLE NOT NULL DEFAULT 0,
    fees_total DOUBLE NOT NULL DEFAULT 0,
    last_price DOUBLE NULL,
    last_price_at TIMESTAMP NULL DEFAULT NULL,
    last_market_cap_basic BIGINT NULL,
    last_change_pct DOUBLE NULL,
    last_perf_w DOUBLE NULL,
    last_perf_1m DOUBLE NULL,
    last_perf_y DOUBLE NULL,
    entry_currency VARCHAR(8) NULL,
    entry_fx_to_portfolio DOUBLE NULL,
    target_weight_pct DOUBLE NULL,
    risk_limit_pct DOUBLE NULL,
    target_price DOUBLE NULL,
    stop_loss_price DOUBLE NULL,
    thesis VARCHAR(2048) NULL,
    notes VARCHAR(1024) NULL,
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP NULL DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_portfolio_company (portfolio_id, company_internal_id),
    FOREIGN KEY (portfolio_id) REFERENCES portfolio_registry(portfolio_id),
    FOREIGN KEY (company_internal_id) REFERENCES trading_view_company_data_map(internal_id)
);
-- @block
CREATE INDEX idx_portfolio_managements_data_v1_portfolio ON portfolio_managements_data_v1(portfolio_id);
CREATE INDEX idx_portfolio_managements_data_v1_company ON portfolio_managements_data_v1(company_internal_id);
CREATE INDEX idx_portfolio_managements_data_v1_status ON portfolio_managements_data_v1(status);
-- @block  Transaction log for audit trail
CREATE TABLE IF NOT EXISTS portfolio_transactions (
    portfolio_transaction_id INT PRIMARY KEY AUTO_INCREMENT,
    portfolio_id INT NOT NULL,
    portfolio_item_id INT NULL,
    company_internal_id INT NOT NULL,
    transaction_type VARCHAR(32) NOT NULL,
    shares_delta DOUBLE NOT NULL DEFAULT 0,
    quantity_value DOUBLE NULL,
    quantity_unit VARCHAR(16) NULL,
    price DOUBLE NULL,
    fees DOUBLE NOT NULL DEFAULT 0,
    cash_flow DOUBLE NULL,
    amount_currency VARCHAR(8) NULL,
    fx_rate_to_portfolio DOUBLE NULL,
    note VARCHAR(1024),
    external_reference VARCHAR(128),
    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (portfolio_id) REFERENCES portfolio_registry(portfolio_id),
    FOREIGN KEY (portfolio_item_id) REFERENCES portfolio_managements_data_v1(portfolio_item_id),
    FOREIGN KEY (company_internal_id) REFERENCES trading_view_company_data_map(internal_id)
);
-- @block
CREATE INDEX idx_portfolio_transactions_portfolio ON portfolio_transactions(portfolio_id);
CREATE INDEX idx_portfolio_transactions_company ON portfolio_transactions(company_internal_id);
CREATE INDEX idx_portfolio_transactions_executed_at ON portfolio_transactions(executed_at);
-- @block
SELECT *
FROM portfolio_managements_data_v1;