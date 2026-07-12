CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_jieba;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'jiebacfg') THEN
        CREATE TEXT SEARCH CONFIGURATION jiebacfg (PARSER = jieba);
        ALTER TEXT SEARCH CONFIGURATION jiebacfg
            ADD MAPPING FOR n, v, a, i, e, l WITH simple;
    END IF;
END$$;
