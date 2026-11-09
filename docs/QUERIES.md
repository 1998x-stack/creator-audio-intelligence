# Local research queries

Open the local database:

```bash
sqlite3 data/creator_intel.sqlite3
```

## Videos and current pipeline stages

```sql
SELECT v.platform_video_id, v.title, v.source_url, v.status, v.attempts
FROM videos AS v ORDER BY v.id DESC LIMIT 50;
```

## Historical forecasts and exact evidence

```sql
SELECT v.source_url, v.published_at, c.statement, c.evidence_quote,
       c.metric, c.direction, c.horizon, c.conditions
FROM claims AS c JOIN videos AS v ON v.id = c.video_id
WHERE c.kind = 'forecast'
ORDER BY v.published_at DESC, v.id DESC;
```

## Evidence segments for a selected claim

```sql
SELECT s.segment_id, s.start_ms, s.end_ms, s.text
FROM claims AS c
JOIN llm_runs AS lr ON lr.id = c.llm_run_id
JOIN transcript_segments AS s ON s.transcript_id = lr.transcript_id
WHERE c.id = 1
  AND s.segment_id IN (
    SELECT value FROM json_each(c.evidence_segment_ids_json)
  )
ORDER BY s.start_ms;
```

## Operational quality dashboard

```sql
SELECT v.status, count(*) AS video_count FROM videos AS v
GROUP BY v.status ORDER BY video_count DESC;

SELECT qc_reason, count(*) AS captures
FROM captures GROUP BY qc_reason;
```

**Interpretation caution:** Relative forecast dates require a verified publication date; the schema currently accepts NULL published_at. A non-empty metric/direction is an extracted claim, not verified economic correctness. Final publication and quote rights must be reviewed before sharing full corpus.
