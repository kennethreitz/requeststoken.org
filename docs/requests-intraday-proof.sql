-- NOT EXECUTED. GoogleSQL, US location.
-- Supply fixed TIMESTAMP parameters @start_utc and @end_utc.
-- First run a dry run. Actual execution requires an authorized project,
-- authentication, and an explicit maximumBytesBilled limit.
-- MAX(timestamp) describes the newest returned event, not completeness.
SELECT
  CURRENT_TIMESTAMP() AS queried_at_utc,
  @start_utc AS window_start_utc,
  @end_utc AS window_end_utc,
  COUNT(*) AS recorded_distribution_downloads,
  MAX(timestamp) AS latest_observed_event_utc,
  TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(timestamp), SECOND)
    AS latest_observed_event_age_seconds
FROM `bigquery-public-data.pypi.file_downloads`
WHERE project = 'requests'
  AND timestamp >= @start_utc
  AND timestamp < @end_utc
  AND REGEXP_CONTAINS(file.filename, r'(?i)\.(whl|tar\.gz|zip)$');
