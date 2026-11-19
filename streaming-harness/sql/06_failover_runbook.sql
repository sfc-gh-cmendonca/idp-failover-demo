-- ============================================================================
-- IDP_FG failover / failback runbook
--   Frankfurt  = SFSEEUROPE.EU_DEMO59          (normal primary)
--   Ireland    = SFSEEUROPE.CMENDONCA_DEMO_IR  (normal secondary)
--   EC2 shell  = ssh -i keys/idp_harness_ssh ec2-user@3.252.136.74
-- Both accounts have IDP_ADMIN.PUBLIC.PROMOTE_IDP() (sql/07_promote_procedure.sql).
-- ============================================================================

-- =========================== FAILOVER: Frankfurt -> Ireland =================
-- 1. Declare + stop writers (EC2):            idp-target status ; sudo systemctl stop idp-harness
-- 2. (Ireland) note the replication watermark
USE ROLE ACCOUNTADMIN;  USE WAREHOUSE IDP_ADMIN_WH;
SHOW FAILOVER GROUPS LIKE 'IDP_FG';
SELECT MAX(event_ts) AS replicated_up_to, COUNT(*) AS rows_ FROM IDP_DEMO.STREAMING.PRODUCT_PAYLOAD;
-- 3. (Ireland) promote + grants + interactive warehouse
CALL IDP_ADMIN.PUBLIC.PROMOTE_IDP();
-- 4. Redirect + idempotent replay (EC2):      idp-target ireland
--    New channels start fresh (offsets are not replicated). Rows are full snapshots
--    with an epoch-us PRODUCT_VERSION, so VERSION BY keeps the newest per PRODUCT_ID.
-- 5. (Ireland) validate
SELECT DATEDIFF('second', MAX(event_ts), SYSDATE()) AS seconds_behind,
       COUNT_IF(event_ts > DATEADD('minute', -1, SYSDATE())) AS rows_last_minute
FROM IDP_DEMO.STREAMING.PRODUCT_PAYLOAD;
SHOW CHANNELS IN SCHEMA IDP_DEMO.STREAMING;
-- Frankfurt is now the secondary and refreshes from Ireland every 10 minutes automatically.

-- =========================== FAILBACK: Ireland -> Frankfurt =================
-- 6. (Frankfurt) wait until the secondary has caught up; force one refresh
ALTER FAILOVER GROUP IDP_FG REFRESH;
SELECT MAX(event_ts) FROM IDP_DEMO.STREAMING.PRODUCT_PAYLOAD;  -- compare with Ireland
-- 7. Stop writers (EC2):                      sudo systemctl stop idp-harness
-- 8. (Frankfurt) final refresh, then promote back
ALTER FAILOVER GROUP IDP_FG REFRESH;
CALL IDP_ADMIN.PUBLIC.PROMOTE_IDP();
-- 9. Redirect writers back (EC2):              idp-target frankfurt
-- 10. (Ireland) re-enable its scheduled refresh (suspended by PROMOTE_IDP at failover)
--     and stop the interactive warehouse once the 24 h minimum allows it:
ALTER FAILOVER GROUP IDP_FG RESUME;
ALTER WAREHOUSE IDP_IW_XS SUSPEND;
-- 11. (Frankfurt) validate with the step-5 query.
