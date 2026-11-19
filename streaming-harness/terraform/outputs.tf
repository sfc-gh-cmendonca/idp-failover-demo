output "instance_id" {
  value = aws_instance.harness.id
}

output "public_ip" {
  value = aws_instance.harness.public_ip
}

output "ssh" {
  value = "ssh -i ../keys/idp_harness_ssh ec2-user@${aws_instance.harness.public_ip}"
}

output "install_key" {
  description = "Copy the Snowflake private key to the instance and start streaming"
  value       = "scp -i ../keys/idp_harness_ssh ../keys/rsa_key.p8 ec2-user@${aws_instance.harness.public_ip}:/tmp/rsa_key.p8 && ssh -i ../keys/idp_harness_ssh ec2-user@${aws_instance.harness.public_ip} 'sudo install -o idp -g idp -m 600 /tmp/rsa_key.p8 /opt/idp-harness/keys/rsa_key.p8 && rm /tmp/rsa_key.p8 && sudo systemctl restart idp-harness'"
}

output "logs" {
  value = "ssh -i ../keys/idp_harness_ssh ec2-user@${aws_instance.harness.public_ip} sudo journalctl -u idp-harness -f"
}
