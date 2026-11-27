-- Persistent hard limits for the Helius Standard WebSocket fallback.
-- Daily: 256 KiB streamed, 200 frames, 25 getTransaction lookups.
-- Monthly: 5 MiB streamed, 4,000 frames, 500 getTransaction lookups.
CREATE TABLE IF NOT EXISTS sniper.helius_stream_budget (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    utc_day date NOT NULL,
    utc_month date NOT NULL,
    messages_day integer NOT NULL DEFAULT 0,
    messages_month integer NOT NULL DEFAULT 0,
    bytes_day bigint NOT NULL DEFAULT 0,
    bytes_month bigint NOT NULL DEFAULT 0,
    lookups_day integer NOT NULL DEFAULT 0,
    lookups_month integer NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.now()
);

REVOKE ALL ON TABLE sniper.helius_stream_budget FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.sniper_helius_budget_consume(
    p_bytes bigint DEFAULT 0,
    p_lookup boolean DEFAULT false
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    v_day date := (pg_catalog.now() AT TIME ZONE 'UTC')::date;
    v_month date := pg_catalog.date_trunc('month', pg_catalog.now() AT TIME ZONE 'UTC')::date;
    v_row record;
    v_messages_day integer;
    v_messages_month integer;
    v_bytes_day bigint;
    v_bytes_month bigint;
    v_lookups_day integer;
    v_lookups_month integer;
BEGIN
    IF p_bytes IS NULL OR p_bytes < 0 OR p_bytes > 262144 THEN
        RETURN pg_catalog.jsonb_build_object('allowed', false, 'reason', 'invalid_frame_size');
    END IF;

    INSERT INTO sniper.helius_stream_budget(singleton, utc_day, utc_month)
    VALUES (true, v_day, v_month)
    ON CONFLICT (singleton) DO NOTHING;

    SELECT * INTO v_row
    FROM sniper.helius_stream_budget
    WHERE singleton = true
    FOR UPDATE;

    v_messages_day := CASE WHEN v_row.utc_day = v_day THEN v_row.messages_day ELSE 0 END;
    v_bytes_day := CASE WHEN v_row.utc_day = v_day THEN v_row.bytes_day ELSE 0 END;
    v_lookups_day := CASE WHEN v_row.utc_day = v_day THEN v_row.lookups_day ELSE 0 END;
    v_messages_month := CASE WHEN v_row.utc_month = v_month THEN v_row.messages_month ELSE 0 END;
    v_bytes_month := CASE WHEN v_row.utc_month = v_month THEN v_row.bytes_month ELSE 0 END;
    v_lookups_month := CASE WHEN v_row.utc_month = v_month THEN v_row.lookups_month ELSE 0 END;

    IF v_messages_day + 1 > 200 OR v_messages_month + 1 > 4000 THEN
        RETURN pg_catalog.jsonb_build_object('allowed', false, 'reason', 'message_budget_exhausted');
    END IF;
    IF v_bytes_day + p_bytes > 262144 OR v_bytes_month + p_bytes > 5242880 THEN
        RETURN pg_catalog.jsonb_build_object('allowed', false, 'reason', 'byte_budget_exhausted');
    END IF;
    IF p_lookup AND (v_lookups_day + 1 > 25 OR v_lookups_month + 1 > 500) THEN
        RETURN pg_catalog.jsonb_build_object('allowed', false, 'reason', 'lookup_budget_exhausted');
    END IF;

    UPDATE sniper.helius_stream_budget
    SET utc_day = v_day,
        utc_month = v_month,
        messages_day = v_messages_day + 1,
        messages_month = v_messages_month + 1,
        bytes_day = v_bytes_day + p_bytes,
        bytes_month = v_bytes_month + p_bytes,
        lookups_day = v_lookups_day + CASE WHEN p_lookup THEN 1 ELSE 0 END,
        lookups_month = v_lookups_month + CASE WHEN p_lookup THEN 1 ELSE 0 END,
        updated_at = pg_catalog.now()
    WHERE singleton = true;

    RETURN pg_catalog.jsonb_build_object(
        'allowed', true,
        'messagesDay', v_messages_day + 1,
        'messagesMonth', v_messages_month + 1,
        'bytesDay', v_bytes_day + p_bytes,
        'bytesMonth', v_bytes_month + p_bytes,
        'lookupsDay', v_lookups_day + CASE WHEN p_lookup THEN 1 ELSE 0 END,
        'lookupsMonth', v_lookups_month + CASE WHEN p_lookup THEN 1 ELSE 0 END
    );
END;
$function$;

REVOKE ALL ON FUNCTION public.sniper_helius_budget_consume(bigint, boolean) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_helius_budget_consume(bigint, boolean) TO service_role;
