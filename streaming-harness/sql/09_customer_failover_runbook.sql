-- =============================================================================
-- IDP Business Continuity: Snowflake Failover Group runbook
--   Primary   : SFSEEUROPE.EU_DEMO59          (AWS eu-central-1, Frankfurt)
--   Secondary : SFSEEUROPE.CMENDONCA_DEMO_IR  (AWS eu-west-1,    Ireland)
--   Replicated: database IDP_DEMO (interactive table PRODUCT_PAYLOAD + pipe)
-- Both accounts: Business Critical edition, run as ACCOUNTADMIN.
-- =============================================================================


-- =============================================================================
-- 1. SET UP REPLICATION (one-time)
-- =============================================================================

-- 1a. [Frankfurt] Create the primary failover group
CREATE FAILOVER GROUP IDP_FG
  OBJECT_TYPES         = DATABASES
  ALLOWED_DATABASES    = IDP_DEMO
  ALLOWED_ACCOUNTS     = SFSEEUROPE.CMENDONCA_DEMO_IR
  REPLICATION_SCHEDULE = '10 MINUTE';

-- 1b. [Ireland] Create the secondary replica (first refresh starts automatically)
CREATE FAILOVER GROUP IDP_FG
  AS REPLICA OF SFSEEUROPE.EU_DEMO59.IDP_FG;

-- 1c. [Ireland] Serving warehouse, kept suspended until it is needed
--     (warehouses, roles and users are account-local: create them in both regions)
CREATE INTERACTIVE WAREHOUSE IDP_IW_XS WAREHOUSE_SIZE = 'XSMALL';
ALTER WAREHOUSE IDP_IW_XS SUSPEND;

-- 1d. [Either] Monitor replication
SHOW FAILOVER GROUPS LIKE 'IDP_FG';

SELECT phase_name, start_time, end_time, primary_snapshot_timestamp
FROM TABLE(IDP_DEMO.INFORMATION_SCHEMA.REPLICATION_GROUP_REFRESH_HISTORY('IDP_FG'))
ORDER BY start_time DESC LIMIT 5;


-- =============================================================================
-- 2. FAILOVER: Frankfurt -> Ireland
-- =============================================================================

-- 2a. Stop writers to Frankfurt (integration harness / ingestion)

-- 2b. [Ireland] Record the last replication point (start of the RPO window)
SELECT MAX(primary_snapshot_timestamp) AS last_replicated_snapshot
FROM TABLE(IDP_DEMO.INFORMATION_SCHEMA.REPLICATION_GROUP_REFRESH_HISTORY('IDP_FG'))
WHERE phase_name = 'COMPLETED';

-- 2c. [Ireland] Promote Ireland to primary
ALTER FAILOVER GROUP IDP_FG SUSPEND;    -- pause scheduled refresh (a running refresh blocks promotion)
ALTER FAILOVER GROUP IDP_FG PRIMARY;    -- Ireland is now read-write; Frankfurt becomes read-only

-- 2d. [Ireland] Re-apply account-local grants on the now-writable objects
GRANT INSERT, SELECT ON TABLE IDP_DEMO.STREAMING.PRODUCT_PAYLOAD TO ROLE IDP_STREAMING_RL;
GRANT OPERATE, MONITOR ON PIPE IDP_DEMO.STREAMING.PRODUCT_PAYLOAD_PIPE TO ROLE IDP_STREAMING_RL;
GRANT SELECT ON TABLE IDP_DEMO.STREAMING.PRODUCT_PAYLOAD TO ROLE IDP_LOADTEST_RL;

-- 2e. [Ireland] Bring up serving compute
ALTER WAREHOUSE IDP_IW_XS ADD TABLES (IDP_DEMO.STREAMING.PRODUCT_PAYLOAD);
ALTER WAREHOUSE IDP_IW_XS RESUME;

-- 2f. Replay source messages produced after the snapshot in 2b, then reconcile
SELECT COUNT(*) AS rows_, MAX(event_ts) AS newest_event
FROM IDP_DEMO.STREAMING.PRODUCT_PAYLOAD;

SELECT COUNT(*) AS duplicate_keys
FROM (SELECT product_id, product_version
      FROM IDP_DEMO.STREAMING.PRODUCT_PAYLOAD
      GROUP BY 1, 2 HAVING COUNT(*) > 1);

-- 2g. Repoint ingestion, then client traffic, to the Ireland account


-- =============================================================================
-- 3. FAILBACK: Ireland -> Frankfurt (planned, zero data loss)
-- =============================================================================

-- 3a. Stop writers to Ireland

-- 3b. [Frankfurt] Pull the final changes from Ireland and verify
ALTER FAILOVER GROUP IDP_FG REFRESH;

SELECT COUNT(*) AS rows_, MAX(event_ts) AS newest_event
FROM IDP_DEMO.STREAMING.PRODUCT_PAYLOAD;    -- must match Ireland exactly

-- 3c. [Frankfurt] Promote Frankfurt back to primary
ALTER FAILOVER GROUP IDP_FG PRIMARY;
--   then re-apply the grants from 2d in Frankfurt

-- 3d. [Ireland] Resume scheduled replication from Frankfurt; release serving compute
ALTER FAILOVER GROUP IDP_FG RESUME;
ALTER WAREHOUSE IDP_IW_XS SUSPEND;

-- 3e. Repoint ingestion and client traffic back to Frankfurt
