-- ============================================================================
-- Secondary side: SFSEEUROPE.CMENDONCA_DEMO_IR (AWS eu-west-1)
-- Run in: CMENDONCA_DEMO_IR as ACCOUNTADMIN (user CHRIS), after 04_failover_primary.sql.
-- ============================================================================
USE ROLE ACCOUNTADMIN;

-- 1. Local objects needed only after failover (not replicated: roles/users excluded)
CREATE ROLE IF NOT EXISTS IDP_STREAMING_RL;
CREATE USER IF NOT EXISTS IDP_STREAMING_SVC
  TYPE = SERVICE
  DEFAULT_ROLE = IDP_STREAMING_RL
  -- same key pair as the primary, so the harness only changes SF_ACCOUNT on failover
  RSA_PUBLIC_KEY = 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA2bLv3AWw7IHRF0ZjAQKADL8Y5hGG72uW868k1RBhZKV+yghZMyWaHs9eJnmkeriK39jv5Vjg5v7Jeuxomg5Trm3Ag4amVpNV3skRFL0bJF62g8klx7paJ5tQ6Qy0FwkTvnTEs6/opb6Ua25mhO2S/Bm1bXaFQLU+rqT1e3PPSci0IToijH58mB6SOT/q8UKmOyTApjk058gSUZhnlXk9PXiFWIJjyQ+4O6Svz5iIliA1WAMH8L5V2z3hnfmCKoDT9Ojx4Mmo9N81COzsM9D/IXVGQuJ1/k1g78ydEc3ZbLNPbThrXczF8Cre+1QgKmFNSSDJw8FKs4AIrYgZ1QRRxQIDAQAB';
GRANT ROLE IDP_STREAMING_RL TO USER IDP_STREAMING_SVC;
GRANT ROLE IDP_STREAMING_RL TO ROLE SYSADMIN;

-- Network rules live in a database; use a local admin DB (the replicated IDP_DEMO is read-only)
CREATE DATABASE IF NOT EXISTS IDP_ADMIN;
CREATE NETWORK RULE IF NOT EXISTS IDP_ADMIN.PUBLIC.IDP_HARNESS_INGRESS_RULE
  TYPE = IPV4 MODE = INGRESS
  VALUE_LIST = ('3.252.136.74', '52.183.42.53')
  COMMENT = 'IDP streaming harness EC2 + operator IP';
CREATE NETWORK POLICY IF NOT EXISTS IDP_STREAMING_SVC_POLICY
  ALLOWED_NETWORK_RULE_LIST = ('IDP_ADMIN.PUBLIC.IDP_HARNESS_INGRESS_RULE');
ALTER USER IDP_STREAMING_SVC SET NETWORK_POLICY = IDP_STREAMING_SVC_POLICY;

CREATE WAREHOUSE IF NOT EXISTS IDP_ADMIN_WH
  WAREHOUSE_SIZE = 'XSMALL' AUTO_SUSPEND = 60 INITIALLY_SUSPENDED = TRUE;

-- 2. Secondary failover group (triggers the initial refresh automatically)
CREATE FAILOVER GROUP IF NOT EXISTS IDP_FG
  AS REPLICA OF SFSEEUROPE.EU_DEMO59.IDP_FG;

-- 3. Interactive warehouse for serving after failover. Created suspended-by-hand:
--    it has a 24 h minimum once resumed. Tables are added after promotion (06).
CREATE INTERACTIVE WAREHOUSE IF NOT EXISTS IDP_IW_XS WAREHOUSE_SIZE = 'XSMALL';
ALTER WAREHOUSE IDP_IW_XS SUSPEND;

-- 4. Monitor replication
SHOW FAILOVER GROUPS;
SELECT phase_name, start_time, end_time, total_bytes, object_count
FROM TABLE(INFORMATION_SCHEMA.REPLICATION_GROUP_REFRESH_HISTORY('IDP_FG'))
ORDER BY start_time DESC LIMIT 5;

-- Replication lag: how far the Ireland copy is behind live streaming
USE WAREHOUSE IDP_ADMIN_WH;
SELECT MAX(event_ts) AS replicated_up_to,
       DATEDIFF('minute', MAX(event_ts), SYSDATE()) AS lag_minutes,
       COUNT(*) AS rows_
FROM IDP_DEMO.STREAMING.PRODUCT_PAYLOAD;
