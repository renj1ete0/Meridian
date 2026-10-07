-- What `make leak-check` compares before and after the suite: every table's row count, and the
-- contents of the configuration a test might change and forget to restore. Columns ending in
-- `_at` are left out, since a restore can rightly move a timestamp.
WITH config AS (
  SELECT 'topic_config' AS name, to_jsonb(t) AS row FROM topic_config t
  UNION ALL SELECT 'agents', to_jsonb(t) FROM agents t
  UNION ALL SELECT 'budget_config', to_jsonb(t) FROM budget_config t
  UNION ALL SELECT 'fetch_policy', to_jsonb(t) FROM fetch_policy t WHERE domain = '*'
  UNION ALL SELECT 'scheduled_jobs', to_jsonb(t) FROM scheduled_jobs t
),
stable AS (
  SELECT name, (SELECT jsonb_object_agg(k, v) FROM jsonb_each(row) AS e(k, v)
                WHERE k NOT LIKE '%\_at' AND k NOT LIKE 'last\_%'
                  AND k NOT IN ('claimed_by', 'claimed_until', 'consecutive_failures')) AS row
  FROM config
)
SELECT line FROM (
  SELECT format('%s rows %s', table_name,
    (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I', table_name),
                                         false, true, '')))[1]::text) AS line
  FROM information_schema.tables
  WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
  UNION ALL
  SELECT name || ' ' || row::text FROM stable
) s
ORDER BY line;
