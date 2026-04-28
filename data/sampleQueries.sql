SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name;


analysis_snapshots
ingestion_issues
normalized_metrics
recommendations
report_snapshots
resources
review_decisions
source_cursors
sqlite_sequence


SELECT * FROM analysis_snapshots ORDER BY created_at_utc DESC;


SELECT * FROM normalized_metrics ORDER BY timestamp_utc DESC;

SELECT resource_id, metric_name, COUNT(*) AS point_count
FROM normalized_metrics
GROUP BY resource_id, metric_name
ORDER BY resource_id, metric_name;


SELECT * FROM report_snapshots ORDER BY created_at_utc DESC LIMIT 1;


SELECT * FROM source_cursors;

SELECT source, health_json, last_window_start_utc, last_window_end_utc FROM source_cursors ORDER BY source;

SELECT metric_name, COUNT(*) AS c
    FROM normalized_metrics
    WHERE source = 'sumologic'
    GROUP BY metric_name
    ORDER BY metric_name
	
	
	SELECT resource_id, metric_name, timestamp_utc, value, unit, source
    FROM normalized_metrics
    WHERE source = 'sumologic'
    ORDER BY timestamp_utc DESC
    LIMIT 20
	

