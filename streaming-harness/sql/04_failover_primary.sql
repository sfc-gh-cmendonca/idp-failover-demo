-- ============================================================================
-- Failover group: SFSEEUROPE.EU_DEMO59 (AWS eu-central-1, primary)
--              -> SFSEEUROPE.CMENDONCA_DEMO_IR (AWS eu-west-1, secondary)
-- Run in: EU_DEMO59, AFTER it has been upgraded to Business Critical
--         (failover can take up to 12 h to become available after the upgrade).
-- Already done: ENABLE_ACCOUNT_DATABASE_REPLICATION = true on both accounts.
-- ============================================================================
USE ROLE ACCOUNTADMIN;

-- Only the IDP database is replicated. USERS / ROLES / WAREHOUSES are deliberately
-- left out: EU_DEMO59 is a shared SE account, and replicating users would also drop
-- target-only users (e.g. CHRIS) on the first refresh. The streaming user, role,
-- warehouse and network policy are created locally in the target (05_failover_secondary.sql).
CREATE FAILOVER GROUP IF NOT EXISTS IDP_FG
  OBJECT_TYPES        = DATABASES
  ALLOWED_DATABASES   = IDP_DEMO
  ALLOWED_ACCOUNTS    = SFSEEUROPE.CMENDONCA_DEMO_IR
  REPLICATION_SCHEDULE = '10 MINUTE';   -- RPO ~10-20 min

SHOW FAILOVER GROUPS;
SHOW DATABASES IN FAILOVER GROUP IDP_FG;
