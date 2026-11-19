variable "aws_region" {
  description = "AWS region for the harness (eu-west-1 keeps it next to EU_DEMO59)"
  type        = string
  default     = "eu-west-1"
}

variable "owner" {
  description = "Owner tag"
  type        = string
  default     = "christophe.mendonca"
}

variable "name" {
  description = "Name prefix for AWS resources"
  type        = string
  default     = "idp-streaming-harness"
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}

variable "subnet_id" {
  description = "Subnet with outbound internet (public subnet or NAT). Empty = first default-VPC subnet."
  type        = string
  default     = ""
}

variable "ssh_public_key_path" {
  description = "SSH public key installed on the instance"
  type        = string
  default     = "../keys/idp_harness_ssh.pub"
}

variable "ssh_allowed_cidr" {
  description = "CIDR allowed to SSH to the instance (your public IP /32)"
  type        = string
}

# --- Snowflake / harness settings ---------------------------------------------

variable "sf_account" {
  description = "Primary Snowflake account (Frankfurt)"
  type        = string
  default     = "SFSEEUROPE-EU_DEMO59"
}

variable "sf_account_secondary" {
  description = "Secondary / failover Snowflake account (Ireland)"
  type        = string
  default     = "SFSEEUROPE-CMENDONCA_DEMO_IR"
}

variable "sf_target" {
  description = "Which account the harness streams to at boot: primary or secondary"
  type        = string
  default     = "primary"
  validation {
    condition     = contains(["primary", "secondary"], var.sf_target)
    error_message = "sf_target must be primary or secondary."
  }
}

variable "sf_user" {
  type    = string
  default = "IDP_STREAMING_SVC"
}

variable "sf_role" {
  type    = string
  default = "IDP_STREAMING_RL"
}

variable "sf_database" {
  type    = string
  default = "IDP_DEMO"
}

variable "sf_schema" {
  type    = string
  default = "STREAMING"
}

variable "sf_pipe" {
  type    = string
  default = "PRODUCT_PAYLOAD_PIPE"
}

variable "rate_per_sec" {
  description = "Messages per second"
  type        = number
  default     = 50
}

variable "universe_size" {
  description = "Number of distinct products (instruments)"
  type        = number
  default     = 50000
}

variable "channels" {
  description = "Snowpipe Streaming channels opened by the producer"
  type        = number
  default     = 2
}
