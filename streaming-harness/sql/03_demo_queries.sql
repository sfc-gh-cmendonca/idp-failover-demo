-- Demo queries for the IDP product payload table
USE ROLE ACCOUNTADMIN;

-- 1. Is data flowing? (standard warehouse)
USE WAREHOUSE IDP_ADMIN_WH;
SELECT COUNT(*) AS rows_, COUNT(DISTINCT product_id) AS products,
       MAX(event_ts) AS last_event,
       DATEDIFF('second', MAX(event_ts), SYSDATE()) AS seconds_behind
FROM IDP_DEMO.STREAMING.PRODUCT_PAYLOAD;

-- Throughput per minute, by event type
SELECT DATE_TRUNC('minute', event_ts) AS minute_, event_type, COUNT(*) AS msgs
FROM IDP_DEMO.STREAMING.PRODUCT_PAYLOAD
WHERE event_ts > DATEADD('minute', -30, SYSDATE())
GROUP BY ALL ORDER BY 1 DESC, 2;

-- Channel health (channels belong to the pipe, so list them at schema level;
-- offset_token should keep increasing on the idp_ec2_* channels)
SHOW CHANNELS IN SCHEMA IDP_DEMO.STREAMING;
SELECT SYSTEM$PIPE_STATUS('IDP_DEMO.STREAMING.PRODUCT_PAYLOAD_PIPE');

-- 2. Low-latency serving on the interactive warehouse (resume it first; 24 h minimum)
-- ALTER WAREHOUSE IDP_IW_XS RESUME;
USE WAREHOUSE IDP_IW_XS;

-- Point lookup: latest state of one product (append-only table: pick highest version)
SELECT product_id, product_version, isin, product_name, price, status, ca_pending, event_ts
FROM IDP_DEMO.STREAMING.PRODUCT_PAYLOAD
WHERE product_id = 'P000000042'
QUALIFY ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY product_version DESC) = 1;

-- Same lookup once the table is CDC-enabled (02_cdc_table.sql): no QUALIFY needed
-- SELECT * FROM IDP_DEMO.STREAMING.PRODUCT_PAYLOAD DEDUP_OPTION = LATEST_DEDUP WHERE product_id = 'P000000042';
-- Full version history:
-- SELECT * FROM IDP_DEMO.STREAMING.PRODUCT_PAYLOAD DEDUP_OPTION = LATEST_ALL WHERE product_id = 'P000000042' ORDER BY product_version;

-- Read straight from the nested UDM payload
SELECT payload:header.productVersion::INT AS version,
       payload:identifiers.isin::STRING   AS isin,
       payload:pricing.price::NUMBER(18,6) AS price,
       payload:settlement.placeOfSettlement::STRING AS settles_at,
       payload:corporateActions.eventType::STRING  AS ca_event
FROM IDP_DEMO.STREAMING.PRODUCT_PAYLOAD
WHERE product_id = 'P000000042'
ORDER BY version DESC LIMIT 5;
