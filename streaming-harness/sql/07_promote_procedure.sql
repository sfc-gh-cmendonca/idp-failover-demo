-- ============================================================================
-- PROMOTE_IDP(): make THIS account the primary for IDP_FG and make it ready to
-- accept streaming. Identical in both accounts, so:
--   failover  = CALL IDP_ADMIN.PUBLIC.PROMOTE_IDP();  in CMENDONCA_DEMO_IR (Ireland)
--   failback  = CALL IDP_ADMIN.PUBLIC.PROMOTE_IDP();  in EU_DEMO59 (Frankfurt)
-- Lives in IDP_ADMIN (local, not replicated). Run as ACCOUNTADMIN.
-- ============================================================================
USE ROLE ACCOUNTADMIN;
CREATE DATABASE IF NOT EXISTS IDP_ADMIN;

CREATE OR REPLACE PROCEDURE IDP_ADMIN.PUBLIC.PROMOTE_IDP()
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
DECLARE
  steps VARCHAR DEFAULT '';
BEGIN
  -- 1. Pause the scheduled refresh (a running refresh blocks promotion), then promote
  BEGIN
    ALTER FAILOVER GROUP IDP_FG SUSPEND;
  EXCEPTION
    WHEN OTHER THEN NULL;   -- already suspended
  END;
  steps := steps || 'suspended; ';
  ALTER FAILOVER GROUP IDP_FG PRIMARY;
  steps := steps || 'promoted; ';

  -- 2. Grants on replicated objects (roles are not replicated, so re-apply locally)
  GRANT USAGE ON DATABASE IDP_DEMO TO ROLE IDP_STREAMING_RL;
  GRANT USAGE ON SCHEMA IDP_DEMO.STREAMING TO ROLE IDP_STREAMING_RL;
  GRANT INSERT, SELECT ON TABLE IDP_DEMO.STREAMING.PRODUCT_PAYLOAD TO ROLE IDP_STREAMING_RL;
  GRANT OPERATE, MONITOR ON PIPE IDP_DEMO.STREAMING.PRODUCT_PAYLOAD_PIPE TO ROLE IDP_STREAMING_RL;
  steps := steps || 'grants applied; ';

  -- 3. Interactive warehouse for serving (created here only if missing; 24 h minimum once running)
  CREATE INTERACTIVE WAREHOUSE IF NOT EXISTS IDP_IW_XS
    TABLES (IDP_DEMO.STREAMING.PRODUCT_PAYLOAD) WAREHOUSE_SIZE = 'XSMALL';
  BEGIN
    ALTER WAREHOUSE IDP_IW_XS ADD TABLES (IDP_DEMO.STREAMING.PRODUCT_PAYLOAD);
  EXCEPTION
    WHEN OTHER THEN NULL;   -- already attached
  END;
  steps := steps || 'IDP_IW_XS ready';

  RETURN steps || '. Next: in the OTHER account run ALTER FAILOVER GROUP IDP_FG RESUME; then idp-target on EC2.';
END;
$$;
