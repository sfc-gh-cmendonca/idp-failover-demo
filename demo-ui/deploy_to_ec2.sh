#!/bin/bash
# Provision (or reuse) an EC2 host for the performance test harness and deploy it.
# Re-runnable: existing key pair / security group / tagged instance are reused.
#
# Settings (env vars, all optional):
#   REGION         default eu-central-1 (keep next to the Snowflake account under test)
#   INSTANCE_TYPE  default m5.xlarge
#   KEY_NAME       default rdp-harness-key      KEY_FILE  default ./${KEY_NAME}.pem
#   SG_NAME        default rdp-harness-sg       (SSH + 8501 from your current IP only)
#   VPC_ID / SUBNET_ID  default: default VPC (or the only VPC) and its first subnet
#   INSTANCE_NAME  default rdp-test-harness
#   OPERATOR_CIDRS default <current IP>/32 (space-separated list allowed)
#   SECRETS_FILE   default .streamlit/secrets.toml (copied to the host, never committed)
#
#   --demo         also deploy the failover storyline UI (backend/ + frontend/dist) as
#                  systemd unit idp-demo on :8080, and give it restricted SSH to the
#                  producer EC2 (idp-demo-ctl only). Extra settings:
#     PRODUCER_INSTANCE  default i-0b0f6686e308caeba   PRODUCER_REGION  default eu-west-1
#     PRODUCER_ADMIN_KEY default ../../idp-streaming-harness/keys/idp_harness_ssh
#     DEMO_PASSWORD      default: generated once into keys/demo_password
set -euo pipefail
cd "$(dirname "$0")"
DEMO=0
[ "${1:-}" = "--demo" ] && DEMO=1

REGION="${REGION:-eu-central-1}"
INSTANCE_TYPE="${INSTANCE_TYPE:-m5.xlarge}"
KEY_NAME="${KEY_NAME:-rdp-harness-key}"
KEY_FILE="${KEY_FILE:-./${KEY_NAME}.pem}"
SG_NAME="${SG_NAME:-rdp-harness-sg}"
INSTANCE_NAME="${INSTANCE_NAME:-rdp-test-harness}"
SECRETS_FILE="${SECRETS_FILE:-.streamlit/secrets.toml}"
JAVA_HOME_REMOTE=/usr/lib/jvm/java-17-amazon-corretto.x86_64
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -i "$KEY_FILE")

echo "=== RDP Test Harness - EC2 Deployment ($REGION, $INSTANCE_TYPE) ==="
for f in lib/snowflake-jdbc.jar lib/slf4j-api.jar lib/slf4j-simple.jar keys/rsa_key.p8 "$SECRETS_FILE"; do
    [ -f "$f" ] || { echo "Missing $f (see README Setup)"; exit 1; }
done

# 1. Key pair
if aws ec2 describe-key-pairs --region "$REGION" --key-names "$KEY_NAME" >/dev/null 2>&1; then
    [ -f "$KEY_FILE" ] || { echo "Key pair $KEY_NAME exists but $KEY_FILE not found; set KEY_FILE"; exit 1; }
    echo "[1/6] Reusing key pair $KEY_NAME"
else
    aws ec2 create-key-pair --region "$REGION" --key-name "$KEY_NAME" \
        --query 'KeyMaterial' --output text > "$KEY_FILE"
    chmod 400 "$KEY_FILE"
    echo "[1/6] Created key pair $KEY_NAME -> $KEY_FILE"
fi

# 2. Security group: SSH + Streamlit from the current IP only
MY_IP=$(curl -s https://checkip.amazonaws.com)
# VPC/subnet: VPC_ID / SUBNET_ID env, else the default VPC, else the only VPC in the region
VPC_ID="${VPC_ID:-$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)}"
if [ "$VPC_ID" = "None" ]; then
    VPC_ID=$(aws ec2 describe-vpcs --region "$REGION" --query 'Vpcs[0].VpcId' --output text)
fi
SUBNET_ID="${SUBNET_ID:-$(aws ec2 describe-subnets --region "$REGION" --filters Name=vpc-id,Values="$VPC_ID" \
    --query 'sort_by(Subnets,&AvailabilityZone)[0].SubnetId' --output text)}"
SG_ID=$(aws ec2 describe-security-groups --region "$REGION" \
    --filters Name=group-name,Values="$SG_NAME" Name=vpc-id,Values="$VPC_ID" \
    --query 'SecurityGroups[0].GroupId' --output text)
if [ "$SG_ID" = "None" ]; then
    SG_ID=$(aws ec2 create-security-group --region "$REGION" --group-name "$SG_NAME" \
        --description "RDP Test Harness - SSH and Streamlit from operator IP" \
        --vpc-id "$VPC_ID" --query 'GroupId' --output text)
fi
# OPERATOR_CIDRS: space-separated list if you egress from more than one IP
OPERATOR_CIDRS="${OPERATOR_CIDRS:-${MY_IP}/32}"
for cidr in $OPERATOR_CIDRS; do
    for port in 22 8501; do
        aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG_ID" \
            --protocol tcp --port "$port" --cidr "$cidr" >/dev/null 2>&1 || true
    done
done
echo "[2/6] Security group $SG_ID: 22 + 8501 from $OPERATOR_CIDRS"

# 3. Instance (reuse a RUNNING one with this name tag; stopped instances are never started or terminated)
INSTANCE_ID=$(aws ec2 describe-instances --region "$REGION" \
    --filters Name=tag:Name,Values="$INSTANCE_NAME" Name=instance-state-name,Values=running,pending \
    --query 'Reservations[0].Instances[0].InstanceId' --output text)
if [ "$INSTANCE_ID" = "None" ]; then
    AMI_ID=$(aws ec2 describe-images --region "$REGION" --owners amazon \
        --filters "Name=name,Values=al2023-ami-2023.*-kernel-*-x86_64" Name=state,Values=available \
        --query 'sort_by(Images,&CreationDate)[-1].ImageId' --output text)
    INSTANCE_ID=$(aws ec2 run-instances --region "$REGION" --image-id "$AMI_ID" \
        --instance-type "$INSTANCE_TYPE" --key-name "$KEY_NAME" \
        --network-interfaces "DeviceIndex=0,SubnetId=$SUBNET_ID,Groups=$SG_ID,AssociatePublicIpAddress=true" \
        --metadata-options HttpTokens=required \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$INSTANCE_NAME}]" \
        --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":30,"VolumeType":"gp3","Encrypted":true}}]' \
        --query 'Instances[0].InstanceId' --output text)
    echo "[3/6] Launched $INSTANCE_ID ($AMI_ID)"
else
    echo "[3/6] Reusing running instance $INSTANCE_ID"
fi
aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"
PUBLIC_IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
echo "      Public IP: $PUBLIC_IP"

# 4. Wait for SSH
echo "[4/6] Waiting for SSH..."
until ssh "${SSH_OPTS[@]}" ec2-user@"$PUBLIC_IP" true 2>/dev/null; do sleep 5; done

# 5. Packages
echo "[5/6] Installing Java 17 + Python 3.11 packages..."
ssh "${SSH_OPTS[@]}" ec2-user@"$PUBLIC_IP" 'set -e
sudo dnf install -y -q java-17-amazon-corretto-devel python3.11 python3.11-pip screen
python3.11 -m pip install -q --user streamlit snowflake-connector-python pandas cryptography
mkdir -p ~/rdp_harness/lib ~/rdp_harness/keys ~/rdp_harness/.streamlit ~/rdp_harness/java-harness/src
chmod 700 ~/rdp_harness/keys'

# 6. Code, jars, key, secrets; compile; (re)start Streamlit
echo "[6/6] Deploying harness..."
scp "${SSH_OPTS[@]}" -q app.py adbc_harness.py adbc_traffic.py ec2-user@"$PUBLIC_IP":~/rdp_harness/
scp "${SSH_OPTS[@]}" -q java-harness/src/RdpLoadTest.java ec2-user@"$PUBLIC_IP":~/rdp_harness/java-harness/src/
scp "${SSH_OPTS[@]}" -q lib/*.jar ec2-user@"$PUBLIC_IP":~/rdp_harness/lib/
scp "${SSH_OPTS[@]}" -q keys/rsa_key.p8 ec2-user@"$PUBLIC_IP":~/rdp_harness/keys/
scp "${SSH_OPTS[@]}" -q "$SECRETS_FILE" ec2-user@"$PUBLIC_IP":~/rdp_harness/.streamlit/secrets.toml
ssh "${SSH_OPTS[@]}" ec2-user@"$PUBLIC_IP" "set -e
chmod 600 ~/rdp_harness/keys/rsa_key.p8 ~/rdp_harness/.streamlit/secrets.toml
cd ~/rdp_harness/java-harness && $JAVA_HOME_REMOTE/bin/javac -cp '../lib/*' -d out src/RdpLoadTest.java
cat > ~/rdp_harness/start.sh <<EOF
#!/bin/bash
export JAVA_HOME=$JAVA_HOME_REMOTE
cd ~/rdp_harness
screen -S harness -X quit 2>/dev/null || true
screen -dmS harness bash -c 'python3.11 -m streamlit run app.py --server.port 8501 --server.headless true --server.address 0.0.0.0 > streamlit.log 2>&1'
echo Streamlit started on port 8501
EOF
chmod +x ~/rdp_harness/start.sh && ~/rdp_harness/start.sh"

# 7. (optional) Failover storyline UI
if [ "$DEMO" = 1 ]; then
    PRODUCER_INSTANCE="${PRODUCER_INSTANCE:-i-0b0f6686e308caeba}"
    PRODUCER_REGION="${PRODUCER_REGION:-eu-west-1}"
    PRODUCER_ADMIN_KEY="${PRODUCER_ADMIN_KEY:-../../idp-streaming-harness/keys/idp_harness_ssh}"
    echo "[7/7] Deploying failover storyline UI..."
    [ -f keys/ops_key.p8 ] || { echo "Missing keys/ops_key.p8 (see idp-streaming-harness/sql/08_demo_ops.sql)"; exit 1; }
    [ -f frontend/dist/index.html ] || (cd frontend && npm ci --silent && npm run build)
    [ -f keys/producer_ctl_ssh ] || ssh-keygen -q -t ed25519 -N '' -C idp-demo-ctl -f keys/producer_ctl_ssh
    [ -f keys/demo_password ] || { openssl rand -base64 18 | tr -d '/+=' > keys/demo_password; chmod 600 keys/demo_password; }
    DEMO_PASSWORD="${DEMO_PASSWORD:-$(cat keys/demo_password)}"

    # UI port from operator IPs only
    for cidr in $OPERATOR_CIDRS; do
        aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG_ID" \
            --protocol tcp --port 8080 --cidr "$cidr" >/dev/null 2>&1 || true
    done

    # Producer: allow SSH from this host; pin the demo key to idp-demo-ctl
    PRODUCER_IP=$(aws ec2 describe-instances --region "$PRODUCER_REGION" --instance-ids "$PRODUCER_INSTANCE" \
        --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
    for psg in $(aws ec2 describe-instances --region "$PRODUCER_REGION" --instance-ids "$PRODUCER_INSTANCE" \
            --query 'Reservations[0].Instances[0].SecurityGroups[].GroupId' --output text); do
        aws ec2 authorize-security-group-ingress --region "$PRODUCER_REGION" --group-id "$psg" \
            --protocol tcp --port 22 --cidr "${PUBLIC_IP}/32" >/dev/null 2>&1 || true
    done
    PUB=$(cat keys/producer_ctl_ssh.pub)
    ssh -o StrictHostKeyChecking=accept-new -i "$PRODUCER_ADMIN_KEY" ec2-user@"$PRODUCER_IP" "set -e
        grep -v 'idp-demo-ctl' ~/.ssh/authorized_keys > ~/.ssh/ak.tmp || true
        echo 'command=\"/usr/local/bin/idp-demo-ctl\",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty $PUB' >> ~/.ssh/ak.tmp
        install -m 600 ~/.ssh/ak.tmp ~/.ssh/authorized_keys && rm ~/.ssh/ak.tmp"

    ssh "${SSH_OPTS[@]}" ec2-user@"$PUBLIC_IP" 'mkdir -p ~/rdp_harness/backend ~/rdp_harness/frontend'
    tar czf - --exclude='__pycache__' --exclude='.venv' --exclude='demo_state.json' backend frontend/dist \
        | ssh "${SSH_OPTS[@]}" ec2-user@"$PUBLIC_IP" 'tar xzf - -C ~/rdp_harness'
    scp "${SSH_OPTS[@]}" -q keys/ops_key.p8 keys/producer_ctl_ssh ec2-user@"$PUBLIC_IP":~/rdp_harness/keys/
    ssh "${SSH_OPTS[@]}" ec2-user@"$PUBLIC_IP" "set -e
        chmod 600 ~/rdp_harness/keys/*
        cd ~/rdp_harness/backend
        [ -d .venv ] || python3.11 -m venv .venv
        .venv/bin/pip install -q -r requirements.txt
        printf 'DEMO_PASSWORD=%s\nDEMO_PRODUCER_SSH=ec2-user@%s\n' '$DEMO_PASSWORD' '$PRODUCER_IP' | sudo tee /etc/idp-demo.env >/dev/null
        sudo chmod 600 /etc/idp-demo.env
        sudo tee /etc/systemd/system/idp-demo.service >/dev/null <<'UNIT'
[Unit]
Description=IDP failover storyline UI
After=network-online.target

[Service]
User=ec2-user
EnvironmentFile=/etc/idp-demo.env
WorkingDirectory=/home/ec2-user/rdp_harness/backend
ExecStart=/home/ec2-user/rdp_harness/backend/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
Restart=on-failure
RestartSec=5
KillMode=mixed

[Install]
WantedBy=multi-user.target
UNIT
        sudo systemctl daemon-reload && sudo systemctl enable --now idp-demo && sudo systemctl restart idp-demo"
    echo "  Demo UI:      http://${PUBLIC_IP}:8080   (any user, password in keys/demo_password)"
fi

echo ""
echo "=== DEPLOYMENT COMPLETE ==="
echo "  Instance ID:  $INSTANCE_ID"
echo "  Public IP:    $PUBLIC_IP   (allow it in the Snowflake network policy of the test user)"
echo "  Harness URL:  http://${PUBLIC_IP}:8501"
echo "  SSH:          ssh -i $KEY_FILE ec2-user@${PUBLIC_IP}"
echo "  Stop:         aws ec2 stop-instances --region $REGION --instance-ids $INSTANCE_ID"
echo "  Terminate:    aws ec2 terminate-instances --region $REGION --instance-ids $INSTANCE_ID"
