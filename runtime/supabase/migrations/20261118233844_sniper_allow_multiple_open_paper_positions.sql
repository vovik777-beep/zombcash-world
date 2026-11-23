-- Allow multiple simultaneous paper positions per registered user,
-- while preventing duplicate open rows for the same Mint.
DROP INDEX IF EXISTS sniper.paper_positions_one_open_per_user_idx;

CREATE UNIQUE INDEX IF NOT EXISTS paper_positions_one_open_per_mint_idx
ON sniper.paper_positions (telegram_id, mint)
WHERE status = 'open';
