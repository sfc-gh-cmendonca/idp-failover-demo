data "aws_vpc" "default" {
  count   = var.subnet_id == "" ? 1 : 0
  default = true
}

data "aws_subnets" "default" {
  count = var.subnet_id == "" ? 1 : 0
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default[0].id]
  }
}

data "aws_subnet" "selected" {
  id = var.subnet_id != "" ? var.subnet_id : sort(data.aws_subnets.default[0].ids)[0]
}

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-kernel-*-x86_64"]
  }
  filter {
    name   = "state"
    values = ["available"]
  }
}

# --- Access: SSH from the operator's IP only; egress HTTPS/HTTP ---------------

resource "aws_key_pair" "harness" {
  key_name_prefix = "${var.name}-"
  public_key      = file(var.ssh_public_key_path)
}

resource "aws_security_group" "harness" {
  name_prefix = "${var.name}-"
  vpc_id      = data.aws_subnet.selected.vpc_id
  description = "IDP harness - SSH from operator IP, egress to Snowflake and package repos"

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_allowed_cidr]
    description = "SSH from operator"
  }
  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "dnf package repos"
  }
}

# --- Instance -----------------------------------------------------------------
# Harness code is shipped inside gzip'd user_data (no S3 / IAM role needed).
# The Snowflake private key is NOT in user_data; it is copied over SSH after boot.

resource "aws_instance" "harness" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = var.instance_type
  subnet_id                   = data.aws_subnet.selected.id
  vpc_security_group_ids      = [aws_security_group.harness.id]
  key_name                    = aws_key_pair.harness.key_name
  associate_public_ip_address = true

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    volume_size = 16
    encrypted   = true
  }

  user_data_replace_on_change = false
  user_data_base64 = base64gzip(templatefile("${path.module}/user_data.sh.tftpl", {
    producer_py   = file("${path.module}/../harness/producer.py")
    udm_py        = file("${path.module}/../harness/udm.py")
    journal_py    = file("${path.module}/../harness/journal.py")
    replay_py     = file("${path.module}/../harness/replay.py")
    idp_demo_ctl  = file("${path.module}/../harness/idp-demo-ctl.sh")
    requirements  = file("${path.module}/../harness/requirements.txt")
    idp_target    = file("${path.module}/../harness/idp-target.sh")
    sf_account    = var.sf_account
    sf_account_secondary = var.sf_account_secondary
    sf_target     = var.sf_target
    sf_user       = var.sf_user
    sf_role       = var.sf_role
    sf_database   = var.sf_database
    sf_schema     = var.sf_schema
    sf_pipe       = var.sf_pipe
    rate_per_sec  = var.rate_per_sec
    universe_size = var.universe_size
    channels      = var.channels
  }))

  tags = { Name = var.name }

  # Code/config updates are pushed over SSH to the running instance; replacing it would
  # change its public IP and break the Snowflake network rules in both accounts.
  lifecycle {
    ignore_changes = [ami, user_data, user_data_base64]
  }
}
