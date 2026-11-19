# IDP Product Payload Streaming Harness

Streams synthetic Securities Services **product (instrument reference data) UDM** messages from an EC2
instance in your AWS account into an **interactive table** in `SFSEEUROPE-EU_DEMO59` using the
**Snowpipe Streaming high-performance SDK** (`snowpipe-streaming` 1.9.1). No Kafka in the path.

```
EC2 (eu-west-1, systemd)  --Snowpipe Streaming SDK-->  PRODUCT_PAYLOAD_PIPE  -->  IDP_DEMO.STREAMING.PRODUCT_PAYLOAD
  producer.py + udm.py           (key-pair auth,                               (interactive table)  --> IDP_IW_XS
  key from Secrets Manager        2 channels)                                                        (interactive WH)
```

## Layout

| Path | What |
|---|---|
| `harness/udm.py` | Instrument universe + event generator (valid ISIN/CUSIP/SEDOL check digits) |
| `harness/producer.py` | Rate-paced producer, products hashed to channels so each product's versions stay ordered |
| `terraform/` | EC2 + IAM + Secrets Manager + S3 (code) + egress-only SG; access via SSM |
| `sql/01_setup.sql` | Snowflake objects (already applied) |
| `sql/02_cdc_table.sql` | Switch to a CDC upsert table once the preview is enabled |
| `sql/03_demo_queries.sql` | Monitoring and lookup queries |
| `keys/` | RSA key pair for `IDP_STREAMING_SVC` (git-ignored; public key already registered) |

## Deploy to AWS (as deployed)

The `snowflake1` IAM user cannot create IAM roles, S3 buckets or Secrets Manager secrets, so the
Terraform is EC2-only: harness code ships in gzip'd `user_data`, SSH (port 22) is open to
`ssh_allowed_cidr` only, and the Snowflake private key is copied over SSH after boot (never in user_data).

Snowflake side: the account-level `ACCOUNT_VPN_POLICY_SE` blocks EC2 IPs, so `IDP_STREAMING_SVC` has its
own `IDP_STREAMING_SVC_POLICY` (network rule `IDP_DEMO.STREAMING.IDP_HARNESS_INGRESS_RULE`, EC2 public IP
+ operator IP). If the instance is replaced its public IP changes; update the rule's VALUE_LIST.

```bash
cd terraform
terraform init && terraform apply                 # terraform.tfvars sets ssh_allowed_cidr
eval "$(terraform output -raw install_key)"       # copy key + start service
eval "$(terraform output -raw logs)"              # tail the producer
```

Change rate without redeploying:

```bash
eval "$(terraform output -raw ssh)" \
  "sudo sed -i 's/^RATE_PER_SEC=.*/RATE_PER_SEC=500/' /etc/idp-harness.env && sudo systemctl restart idp-harness"
```

Stop / tear down: `sudo systemctl stop idp-harness` on the instance, or `terraform destroy`.

### Switching region (failover)

`/etc/idp-harness.env` holds both accounts; `SF_TARGET` picks one:

```
SF_TARGET=primary                                 # primary | secondary
SF_ACCOUNT_PRIMARY=SFSEEUROPE-EU_DEMO59           # Frankfurt
SF_ACCOUNT_SECONDARY=SFSEEUROPE-CMENDONCA_DEMO_IR # Ireland
```

On the instance (switches and restarts the service):

```bash
idp-target status
idp-target ireland      # or: secondary
idp-target frankfurt    # or: primary
```

Only stream to Ireland after `ALTER FAILOVER GROUP IDP_FG PRIMARY` has been run there
(see `sql/06_failover_runbook.sql`); a secondary database is read-only.
`PRODUCT_VERSION` is epoch microseconds, so versions written in Ireland always sort after
the replicated ones and `VERSION BY PRODUCT_VERSION` picks the newest snapshot.

Code updates are pushed over SSH (Terraform ignores user_data / AMI changes so the
instance, and its public IP in the Snowflake network rules, is not replaced).

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r harness/requirements.txt
cd harness
SF_ACCOUNT=SFSEEUROPE-EU_DEMO59 PRIVATE_KEY_PATH=../keys/rsa_key.p8 RATE_PER_SEC=100 DURATION_SEC=60 \
  ../.venv/bin/python producer.py
```

| Env var | Default | |
|---|---|---|
| `RATE_PER_SEC` | 100 | messages/second |
| `UNIVERSE_SIZE` | 50000 | distinct products |
| `CHANNELS` | 2 | streaming channels |
| `DURATION_SEC` | 0 | 0 = run until stopped |
| `INSTANCE_ID` | random | channel name prefix; reuse it to resume from committed offsets |

## The UDM (product payload)

Each message is a **full-state snapshot** of one product, keyed by `PRODUCT_ID` and versioned by a
per-product, monotonically increasing `PRODUCT_VERSION`. That makes every message an idempotent upsert,
which is what `PRIMARY KEY ... INITIALLY DEFERRED` + `VERSION BY PRODUCT_VERSION` needs, and it is what
makes replay after a failover safe.

Event mix: first sighting = `CREATE`; then ~80% price moves, ~12% corporate-action flag on/off,
~6% status change (ACTIVE/SUSPENDED), ~2% `DELETE` (soft: DELISTED / MATURED).

Key fields are flattened into columns for fast lookups; the full nested message is kept in `PAYLOAD`:

```json
{
  "header":          {"messageType": "PRODUCT", "schemaVersion": "1.2", "eventType": "UPDATE",
                      "eventTimestamp": "2026-10-30 07:15:22.104311", "sourceSystem": "AMPS-IDP",
                      "productId": "P000000042", "productVersion": 7},
  "identifiers":     {"isin": "US38259P5089", "cusip": "38259P508", "sedol": "B0YBKJ7", "figi": "BBG000BHTMY7", "ticker": "AHQK"},
  "classification":  {"assetClass": "EQUITY", "assetSubclass": "COMMON_STOCK", "cfiCode": "ESVUFR"},
  "issuer":          {"name": "Atlas Holdings", "lei": "5493001KJTIIGC8Y1R12", "countryOfIssue": "US"},
  "terms":           {"currency": "USD", "couponRate": null, "issueDate": "2011-04-18", "maturityDate": null},
  "listing":         {"mic": "XNYS", "status": "ACTIVE"},
  "pricing":         {"price": 412.381552, "currency": "USD", "priceTimestamp": "2026-10-30 07:15:22.104311"},
  "settlement":      {"placeOfSettlement": "DTC", "cycle": "T+1"},
  "corporateActions":{"pending": true, "eventType": "DIVIDEND"}
}
```

## CDC status

`EU_DEMO59` currently rejects `VERSION BY` (Interactive CDC private preview not enabled), so the table is
**append-only** today. Lookups use `QUALIFY ROW_NUMBER() ... ORDER BY product_version DESC = 1`.
Once CDC is enabled, stop the harness, run `sql/02_cdc_table.sql`, and restart; no harness changes needed.

## Cost notes

- `IDP_IW_XS` (interactive) has a 24 h minimum auto-suspend; it was left **suspended**. Resume only for demos.
- Snowpipe Streaming bills per uncompressed GB ingested; at 100 msg/s (~1.7 KB each) that is ~15 GB/day.
- `t3.small` EC2 ~ $0.02/h in eu-west-1.
