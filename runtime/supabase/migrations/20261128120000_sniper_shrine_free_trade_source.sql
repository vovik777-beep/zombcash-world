ALTER TABLE sniper.token_trades
    DROP CONSTRAINT IF EXISTS token_trades_source_check;
ALTER TABLE sniper.token_trades
    ADD CONSTRAINT token_trades_source_check
    CHECK (source IN ('pumpdev','pumpportal','shrine','helius_standard_ws','legacy_unattributed'));
