# RDP Interactive Tables Performance Test Harness

Load test harness for benchmarking Snowflake standard tables, Interactive Tables, and multi-cluster warehouses against a 36B-row time-series observation workload. Supports two connectors, JDBC (Java) and ADBC (Arrow), so you can compare them side by side.

## Architecture

- **Streamlit UI** (`app.py`) — Configuration panel + results dashboard
- **Java JDBC CLI** (`java-harness/src/RdpLoadTest.java`) — Load test engine (called as subprocess)
- **ADBC harness** (`adbc_harness.py`) — Native Python engine using the ADBC Snowflake driver; returns Arrow RecordBatches with no JVM or JDBC chunk downloads. Produces the same results JSON as the Java CLI, so the dashboard renders both identically.
- **EC2 deployment** (`deploy_to_ec2.sh`) — Automated AWS setup script

### Local frontend + API (alternative to Streamlit)

For driving tests from a laptop while the engine stays on EC2 (close to
Snowflake), there is a client/server split that runs alongside `app.py`:

- **`backend/`** — FastAPI service that wraps the Java engine and Snowflake
  metrics as a REST API. Runs on the EC2 host (port 8000). See `backend/README.md`.
- **`frontend/`** — React + Vite + TypeScript SPA that runs locally and calls the
  EC2 API (URL is configurable). Charts via Recharts. See `frontend/README.md`.

```
[ laptop: React frontend ]  --HTTP-->  [ EC2: FastAPI backend ]  --subprocess-->  [ Java JDBC engine ]  --JDBC-->  [ Snowflake ]
```

Quick start:

```bash
# On EC2 (next to the Java engine, jars, and keys)
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && ./run.sh   # serves :8000

# On your laptop
cd frontend && npm install
echo "VITE_API_BASE_URL=http://<EC2_PUBLIC_IP>:8000" > .env
npm run dev                                    # http://localhost:5173
```

Remember to open port 8000 to your laptop's IP on the EC2 security group (the
same pattern `deploy_to_ec2.sh` uses for 8501).

## IDP product point lookup (Interactive Table, JDBC)

Target table `IDP_DEMO.STREAMING.PRODUCT_PAYLOAD` is an append-only interactive table fed by
Snowpipe Streaming (2 KB securities-services product messages). Select
**IDP_DEMO.STREAMING.PRODUCT_PAYLOAD (Interactive - IDP product point lookup)** as the target
table; the query mix and connector are fixed to:

```sql
SELECT PRODUCT_ID, PRODUCT_VERSION, ISIN, PRODUCT_NAME, PRICE, STATUS, PAYLOAD
FROM IDP_DEMO.STREAMING.PRODUCT_PAYLOAD
WHERE PRODUCT_ID = '<random from pool>'
ORDER BY PRODUCT_VERSION DESC LIMIT 1          -- latest version (append-only table)
```

- Engine: JDBC (Java), mix `product_single`. Each query is one single-key lookup served by the
  interactive warehouse (`IDP_IW_XS` by default).
- Key pool: up to 1M `PRODUCT_ID`s sampled from the table on `SNOWFLAKE_METRICS_WAREHOUSE`
  (a standard warehouse); keys are validated as plain identifiers before being inlined.
- Ticker Distribution applies to products: `uniform`, `hot80`, `hot50` (hot = first 1,000 keys).
- Results schema is unchanged (`unique_tickers` = distinct products queried).

Example CLI run (what the dashboard executes):

```bash
java --add-opens=java.base/java.nio=ALL-UNNAMED -cp "java-harness/out:lib/*" RdpLoadTest \
  --account "$SNOWFLAKE_ACCOUNT" --user "$SNOWFLAKE_USER" --key keys/rsa_key.p8 \
  --database IDP_DEMO --schema STREAMING --table IDP_DEMO.STREAMING.PRODUCT_PAYLOAD \
  --warehouse IDP_IW_XS --pool_warehouse IDP_ADMIN_WH --mix product_single \
  --connections 1 --duration 30 --rampup 0 --ticker_bias uniform
```

## Configuration (no hardcoded account/user)

`app.py` reads its Snowflake settings from environment variables, falling back to
`[snowflake]` in `.streamlit/secrets.toml` (git-ignored):

| Env var | secrets.toml key | Default |
|---|---|---|
| `SNOWFLAKE_ACCOUNT` | `account` | required |
| `SNOWFLAKE_USER` | `user` | required |
| `SNOWFLAKE_PRIVATE_KEY_PATH` | `private_key_path` | `keys/rsa_key.p8` |
| `SNOWFLAKE_DATABASE` / `SNOWFLAKE_SCHEMA` | `database` / `schema` | `THEMES_RESEARCH_DEMO` / `RAW_DATA` |
| `SNOWFLAKE_METRICS_WAREHOUSE` | `metrics_warehouse` | `COMPUTE_WH` |
| `SNOWFLAKE_IDP_WAREHOUSE` | `idp_warehouse` | `IDP_IW_XS` |

```toml
# .streamlit/secrets.toml
[snowflake]
account = "SFSEEUROPE-EU_DEMO59"
user = "IDP_LOADTEST_SVC"
private_key_path = "keys/rsa_key.p8"
metrics_warehouse = "IDP_ADMIN_WH"
idp_warehouse = "IDP_IW_XS"
```

The Java engine now requires `--account` and `--user` (no built-in defaults).

## Requirements

- Python 3.11+ with `streamlit`, `snowflake-connector-python`, `pandas`, `cryptography`
- For the ADBC connector: `adbc-driver-snowflake`, `pyarrow`
- Java 17+ (Amazon Corretto recommended)
- Snowflake JDBC driver v4.2.0 (`lib/snowflake-jdbc.jar` — download separately)
- SLF4J jars (`lib/slf4j-api.jar`, `lib/slf4j-simple.jar`)
- RSA key pair for Snowflake key-pair auth

## Setup

1. Download JARs into `lib/`:
   ```bash
   mkdir lib
   curl -L -o lib/snowflake-jdbc.jar "https://repo1.maven.org/maven2/net/snowflake/snowflake-jdbc/4.2.0/snowflake-jdbc-4.2.0.jar"
   curl -L -o lib/slf4j-api.jar "https://repo1.maven.org/maven2/org/slf4j/slf4j-api/2.0.9/slf4j-api-2.0.9.jar"
   curl -L -o lib/slf4j-simple.jar "https://repo1.maven.org/maven2/org/slf4j/slf4j-simple/2.0.9/slf4j-simple-2.0.9.jar"
   ```

2. Generate RSA key pair and assign to Snowflake user:
   ```bash
   mkdir keys
   openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out keys/rsa_key.p8
   openssl rsa -in keys/rsa_key.p8 -pubout -out keys/rsa_key.pub
   # Then in Snowflake: ALTER USER <user> SET RSA_PUBLIC_KEY='<contents of rsa_key.pub without headers>'
   ```

3. Compile Java harness:
   ```bash
   cd java-harness
   javac -cp '../lib/*' -d out src/RdpLoadTest.java
   ```

4. (Optional) Install the ADBC driver:
   ```bash
   pip install adbc-driver-snowflake pyarrow
   ```

5. Run locally:
   ```bash
   JAVA_HOME=/path/to/java17 streamlit run app.py --server.port 8501
   ```

## EC2 Deployment

`deploy_to_ec2.sh` provisions (or reuses) an m5.xlarge running Amazon Linux 2023 in
eu-central-1, next to the Snowflake account, so network latency stays out of the results.
It is re-runnable and configurable via env vars (`REGION`, `INSTANCE_TYPE`, `KEY_NAME`,
`KEY_FILE`, `SG_NAME`, `INSTANCE_NAME`, `SECRETS_FILE`):

- Reuses the key pair / security group if they exist; opens 22 and 8501 to your current IP only.
- Uses the latest AL2023 x86_64 AMI; never starts or terminates stopped instances.
- Installs Corretto 17 + Python 3.11 packages, copies code, `lib/` jars, `keys/rsa_key.p8` and
  `secrets.toml` (scp, never committed), compiles the Java engine and runs Streamlit in `screen`.

```bash
SG_NAME=rdp-harness-idp-sg KEY_FILE=./rdp-harness-key.pem ./deploy_to_ec2.sh
```

Then allow the printed public IP in the test user's Snowflake network policy.

## Configuration Options

- **Connector:** JDBC (Java) / ADBC (Arrow). Both use RSA key-pair auth from `keys/`.

- **Target Table:** Standard (1B/5B/36B), Interactive (36B), various clustering strategies
- **Query Mix:** Multi-ticker, single-ticker, date range, TS_CD-based, configurable percentages
- **Connections:** 1-300 concurrent connections
- **Duration:** 30s-900s
- **Execution Mode:** Sync / Async (8 in-flight per connection). Async is JDBC only; ADBC uses threaded concurrency.
- **Ticker Distribution:** Uniform / Hot Ticker Bias (80%/50% from top 1000)
- **Result Limit:** No limit / 500 / 1000 / 5000 / 10000 rows

## Security

Never commit credentials. `keys/`, `*.pem` and `.streamlit/secrets.toml` are git-ignored. Authentication is key-pair only, with no passwords in code.
