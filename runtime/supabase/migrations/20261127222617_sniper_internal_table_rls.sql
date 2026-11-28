-- These tables are server-internal. The app uses SECURITY DEFINER RPCs owned by postgres,
-- and the runtime uses service_role (BYPASSRLS). No anon/authenticated table grants exist.
-- Enable RLS as defense in depth so a future grant cannot silently expose raw rows.
ALTER TABLE sniper.token_social_status ENABLE ROW LEVEL SECURITY;
ALTER TABLE sniper.demo_paper_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE sniper.helius_stream_budget ENABLE ROW LEVEL SECURITY;
ALTER TABLE sniper.paper_price_marks ENABLE ROW LEVEL SECURITY;
