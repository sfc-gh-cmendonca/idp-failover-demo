-- ============================================================================
-- Failover storyline UI: ops service user + owner's-rights wrapper.
-- Run as ACCOUNTADMIN in BOTH accounts (EU_DEMO59 and CMENDONCA_DEMO_IR).
--   <OPS_PUBLIC_KEY>       contents of keys/ops_key.pub.oneline
--   <LOADTEST_PUBLIC_KEY>  contents of keys/rsa_key.pub.oneline (Ireland only;
--                          IDP_LOADTEST_SVC already exists in Frankfurt)
-- The UI backend only ever calls IDP_ADMIN.PUBLIC.DEMO_OPS(<action>), so the
-- ops role needs no direct privileges on the failover group or the table.
-- ============================================================================
USE ROLE ACCOUNTADMIN;
USE WAREHOUSE IDP_ADMIN_WH;

-- 1. Network: load-harness EC2 (demo backend) + operator egress IPs
CREATE NETWORK RULE IF NOT EXISTS IDP_ADMIN.PUBLIC.IDP_DEMO_OPS_INGRESS_RULE
  TYPE = IPV4 MODE = INGRESS VALUE_LIST = ('3.121.76.106');
ALTER NETWORK RULE IDP_ADMIN.PUBLIC.IDP_DEMO_OPS_INGRESS_RULE
  SET VALUE_LIST = ('3.121.76.106', '213.86.140.86', '213.161.68.226');
CREATE NETWORK POLICY IF NOT EXISTS IDP_DEMO_OPS_SVC_POLICY
  ALLOWED_NETWORK_RULE_LIST = ('IDP_ADMIN.PUBLIC.IDP_DEMO_OPS_INGRESS_RULE');

-- 2. Ops role + user
CREATE ROLE IF NOT EXISTS IDP_DEMO_OPS_RL;
GRANT ROLE IDP_DEMO_OPS_RL TO ROLE SYSADMIN;
GRANT USAGE ON WAREHOUSE IDP_ADMIN_WH TO ROLE IDP_DEMO_OPS_RL;
GRANT USAGE ON DATABASE IDP_ADMIN TO ROLE IDP_DEMO_OPS_RL;
GRANT USAGE ON SCHEMA IDP_ADMIN.PUBLIC TO ROLE IDP_DEMO_OPS_RL;

CREATE USER IF NOT EXISTS IDP_DEMO_OPS_SVC
  TYPE = SERVICE
  DEFAULT_ROLE = IDP_DEMO_OPS_RL
  DEFAULT_WAREHOUSE = IDP_ADMIN_WH
  COMMENT = 'Failover storyline UI backend (load-harness EC2)';
ALTER USER IDP_DEMO_OPS_SVC SET RSA_PUBLIC_KEY = '<OPS_PUBLIC_KEY>';
ALTER USER IDP_DEMO_OPS_SVC SET NETWORK_POLICY = IDP_DEMO_OPS_SVC_POLICY;
GRANT ROLE IDP_DEMO_OPS_RL TO USER IDP_DEMO_OPS_SVC;

-- 3. Owner's-rights wrapper. Actions:
--   status      counts, watermark, last replication snapshot, failover-group role
--   dupes       duplicate (PRODUCT_ID, PRODUCT_VERSION) keys
--   promote     PROMOTE_IDP() + read grants for the load-test role
--   resume_fg / suspend_fg / refresh_fg
--   iw_resume / iw_suspend
CREATE OR REPLACE PROCEDURE IDP_ADMIN.PUBLIC.DEMO_OPS(ACTION VARCHAR)
RETURNS VARIANT
LANGUAGE SQL
EXECUTE AS OWNER
AS
$$
DECLARE
  res VARIANT DEFAULT OBJECT_CONSTRUCT();
  msg VARCHAR;
BEGIN
  CASE (LOWER(:ACTION))
    WHEN 'status' THEN
      SHOW FAILOVER GROUPS LIKE 'IDP_FG';
      LET fg VARIANT := (SELECT OBJECT_CONSTRUCT('is_primary', "is_primary", 'primary', "primary",
                                                'next_scheduled_refresh', "next_scheduled_refresh")
                         FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))
                         WHERE "account_name" = CURRENT_ACCOUNT_NAME() LIMIT 1);
      LET t VARIANT := (SELECT OBJECT_CONSTRUCT('rows', COUNT(*), 'max_event_ts', MAX(EVENT_TS),
                                                'seconds_behind', DATEDIFF('second', MAX(EVENT_TS), SYSDATE()))
                        FROM IDP_DEMO.STREAMING.PRODUCT_PAYLOAD);
      LET r VARIANT := NULL;
      BEGIN
        r := (SELECT OBJECT_CONSTRUCT('phase', PHASE_NAME, 'start_time', START_TIME, 'end_time', END_TIME,
                                      'primary_snapshot_ts', PRIMARY_SNAPSHOT_TIMESTAMP)
              FROM TABLE(IDP_DEMO.INFORMATION_SCHEMA.REPLICATION_GROUP_REFRESH_HISTORY('IDP_FG'))
              WHERE PHASE_NAME = 'COMPLETED'
              ORDER BY START_TIME DESC LIMIT 1);
      EXCEPTION
        WHEN OTHER THEN r := NULL;   -- primary side has no refresh history
      END;
      SHOW WAREHOUSES LIKE 'IDP_IW_XS';
      LET iw VARIANT := (SELECT OBJECT_CONSTRUCT('state', "state", 'clusters', "started_clusters",
                                                 'max_clusters', "max_cluster_count")
                         FROM TABLE(RESULT_SCAN(LAST_QUERY_ID())) LIMIT 1);
      res := OBJECT_CONSTRUCT('account', CURRENT_ACCOUNT_NAME(), 'failover_group', fg,
                              'table', t, 'last_refresh', r, 'iw', iw);
    WHEN 'dupes' THEN
      res := OBJECT_CONSTRUCT('duplicate_keys',
               (SELECT COUNT(*) FROM (SELECT 1 FROM IDP_DEMO.STREAMING.PRODUCT_PAYLOAD
                                      GROUP BY PRODUCT_ID, PRODUCT_VERSION HAVING COUNT(*) > 1)));
    WHEN 'promote' THEN
      -- Idempotent: re-running after a partial step only re-applies the grants.
      -- PROMOTE_IDP() switches the session warehouse (CREATE INTERACTIVE WAREHOUSE),
      -- so nothing after it may need a warehouse (no RESULT_SCAN).
      SHOW FAILOVER GROUPS LIKE 'IDP_FG';
      LET is_primary VARCHAR := (SELECT "is_primary" FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))
                                 WHERE "account_name" = CURRENT_ACCOUNT_NAME() LIMIT 1);
      IF (:is_primary = 'true') THEN
        msg := 'already primary; grants re-applied';
      ELSE
        CALL IDP_ADMIN.PUBLIC.PROMOTE_IDP();
        msg := 'promoted';
      END IF;
      GRANT USAGE ON DATABASE IDP_DEMO TO ROLE IDP_STREAMING_RL;
      GRANT USAGE ON SCHEMA IDP_DEMO.STREAMING TO ROLE IDP_STREAMING_RL;
      GRANT INSERT, SELECT ON TABLE IDP_DEMO.STREAMING.PRODUCT_PAYLOAD TO ROLE IDP_STREAMING_RL;
      GRANT OPERATE, MONITOR ON PIPE IDP_DEMO.STREAMING.PRODUCT_PAYLOAD_PIPE TO ROLE IDP_STREAMING_RL;
      GRANT USAGE ON DATABASE IDP_DEMO TO ROLE IDP_LOADTEST_RL;
      GRANT USAGE ON SCHEMA IDP_DEMO.STREAMING TO ROLE IDP_LOADTEST_RL;
      GRANT SELECT ON TABLE IDP_DEMO.STREAMING.PRODUCT_PAYLOAD TO ROLE IDP_LOADTEST_RL;
      GRANT USAGE ON WAREHOUSE IDP_IW_XS TO ROLE IDP_LOADTEST_RL;
      res := OBJECT_CONSTRUCT('promote', msg);
    WHEN 'resume_fg' THEN
      ALTER FAILOVER GROUP IDP_FG RESUME;
    WHEN 'suspend_fg' THEN
      ALTER FAILOVER GROUP IDP_FG SUSPEND;
    WHEN 'refresh_fg' THEN
      ALTER FAILOVER GROUP IDP_FG REFRESH;
    WHEN 'iw_resume' THEN
      ALTER WAREHOUSE IDP_IW_XS RESUME IF SUSPENDED;
    WHEN 'iw_suspend' THEN
      ALTER WAREHOUSE IDP_IW_XS SUSPEND;
    ELSE
      RETURN OBJECT_CONSTRUCT('error', 'unknown action ' || :ACTION);
  END CASE;
  RETURN OBJECT_INSERT(res, 'action', LOWER(:ACTION), TRUE);
END;
$$;
GRANT USAGE ON PROCEDURE IDP_ADMIN.PUBLIC.DEMO_OPS(VARCHAR) TO ROLE IDP_DEMO_OPS_RL;

-- 4. (Ireland only) load-test reader, mirroring Frankfurt
-- CREATE ROLE IF NOT EXISTS IDP_LOADTEST_RL;
-- GRANT USAGE, MONITOR ON WAREHOUSE IDP_ADMIN_WH TO ROLE IDP_LOADTEST_RL;
-- CREATE NETWORK RULE IF NOT EXISTS IDP_ADMIN.PUBLIC.IDP_LOADTEST_INGRESS_RULE
--   TYPE = IPV4 MODE = INGRESS VALUE_LIST = ('3.121.76.106', '213.86.140.86', '213.161.68.226');
-- CREATE NETWORK POLICY IF NOT EXISTS IDP_LOADTEST_SVC_POLICY
--   ALLOWED_NETWORK_RULE_LIST = ('IDP_ADMIN.PUBLIC.IDP_LOADTEST_INGRESS_RULE');
-- CREATE USER IF NOT EXISTS IDP_LOADTEST_SVC TYPE = SERVICE DEFAULT_ROLE = IDP_LOADTEST_RL
--   DEFAULT_WAREHOUSE = IDP_ADMIN_WH RSA_PUBLIC_KEY = '<LOADTEST_PUBLIC_KEY>'
--   NETWORK_POLICY = IDP_LOADTEST_SVC_POLICY;
-- GRANT ROLE IDP_LOADTEST_RL TO USER IDP_LOADTEST_SVC;
