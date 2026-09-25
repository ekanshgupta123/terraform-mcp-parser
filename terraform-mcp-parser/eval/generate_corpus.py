"""Generate a deliberately messy Terraform module corpus for retrieval eval.

Writes ~15 small modules under eval/corpus/, each a directory with
variables.tf, outputs.tf, main.tf, and (usually) README.md.

The corpus mixes 3 CLEAN reference modules with 12 MESSY ones exhibiting
real-world pathologies:
  - misleading directory names (vpc-networking provisions security groups;
    data-store provisions an RDS database)
  - copy-paste leftover READMEs (team-storage README describes RDS;
    old-logging README describes firewall rules)
  - missing variable descriptions, unhelpful variable names (s3-stuff)
  - overlapping capabilities (two S3 log buckets; three security-group
    modules; two postgres databases)

Output is fully deterministic (fixed content, no randomness) so eval runs
are comparable. Idempotent: wipes eval/corpus/ and regenerates.

Stdlib only -- run with plain `python3 generate_corpus.py`.
"""

from pathlib import Path

OUT_DIR = Path(__file__).parent / "corpus"


def hcl_repr(value):
    """Render a Python value as an HCL literal."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, list):
        return f"[{', '.join(hcl_repr(v) for v in value)}]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        inner = ", ".join(f"{k} = {hcl_repr(v)}" for k, v in value.items())
        return "{ " + inner + " }"
    raise TypeError(f"unsupported default: {value!r}")


def render_variables(variables):
    blocks = []
    for v in variables:
        lines = [f'variable "{v["name"]}" {{']
        if v.get("description"):
            lines.append(f'  description = "{v["description"]}"')
        lines.append(f'  type        = {v.get("type", "string")}')
        if "default" in v:
            lines.append(f'  default     = {hcl_repr(v["default"])}')
        lines.append("}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def render_outputs(outputs):
    blocks = []
    for o in outputs:
        lines = [f'output "{o["name"]}" {{']
        if o.get("description"):
            lines.append(f'  description = "{o["description"]}"')
        lines.append(f'  value       = {o["value"]}')
        lines.append("}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def render_main(resources):
    blocks = []
    for rtype, label in resources:
        blocks.append(f'resource "{rtype}" "{label}" {{\n}}')
    return "\n\n".join(blocks) + "\n"


# ---------------------------------------------------------------------------
# Corpus definition. `mess` notes the deliberate pathology for documentation.
# ---------------------------------------------------------------------------

MODULES = [
    # ---- CLEAN reference modules -----------------------------------------
    {
        "name": "sg-web-firewall",
        "mess": None,
        "resources": [
            ("aws_security_group", "this"),
            ("aws_vpc_security_group_ingress_rule", "https"),
        ],
        "variables": [
            {"name": "name", "description": "Name of the security group.", "type": "string"},
            {"name": "description", "description": "Human-readable description of the security group.", "type": "string", "default": "Managed by Terraform"},
            {"name": "vpc_id", "description": "ID of the VPC the security group belongs to.", "type": "string"},
            {"name": "ingress_cidr_blocks", "description": "CIDR blocks allowed inbound.", "type": "list(string)", "default": []},
            {"name": "ingress_ports", "description": "TCP ports to open inbound.", "type": "list(number)", "default": [443]},
            {"name": "egress_cidr_blocks", "description": "CIDR blocks allowed outbound.", "type": "list(string)", "default": ["0.0.0.0/0"]},
        ],
        "outputs": [
            {"name": "id", "description": "The security group ID.", "value": "aws_security_group.this.id"},
            {"name": "arn", "description": "The security group ARN.", "value": "aws_security_group.this.arn"},
            {"name": "name", "description": "The security group name.", "value": "aws_security_group.this.name"},
        ],
        "readme": (
            "# sg-web-firewall\n\nProvisions an AWS security group for web "
            "servers with configurable inbound and outbound rules. Use it when "
            "you need firewall-style traffic control for EC2 instances: open "
            "specific ports (e.g. 443) to specific CIDR blocks and lock down "
            "everything else.\n"
        ),
    },
    {
        "name": "s3-private-bucket",
        "mess": None,
        "resources": [
            ("aws_s3_bucket", "this"),
            ("aws_s3_bucket_versioning", "this"),
            ("aws_s3_bucket_server_side_encryption_configuration", "this"),
            ("aws_s3_bucket_public_access_block", "this"),
        ],
        "variables": [
            {"name": "bucket_name", "description": "Name of the S3 bucket.", "type": "string"},
            {"name": "versioning_enabled", "description": "Enable object versioning.", "type": "bool", "default": True},
            {"name": "kms_key_id", "description": "KMS key for server-side encryption. Empty means AES256.", "type": "string", "default": ""},
            {"name": "force_destroy", "description": "Allow deleting a non-empty bucket.", "type": "bool", "default": False},
            {"name": "tags", "description": "Tags to apply to the bucket.", "type": "map(string)", "default": {}},
        ],
        "outputs": [
            {"name": "id", "description": "The bucket name.", "value": "aws_s3_bucket.this.id"},
            {"name": "arn", "description": "The bucket ARN.", "value": "aws_s3_bucket.this.arn"},
            {"name": "bucket_domain_name", "description": "The bucket domain name.", "value": "aws_s3_bucket.this.bucket_domain_name"},
        ],
        "readme": (
            "# s3-private-bucket\n\nA production-ready private S3 bucket with "
            "versioning, server-side encryption, and public access blocked. "
            "Use it for private object storage: backups, Terraform state, "
            "artifacts, or any files that must stay encrypted and "
            "non-public.\n"
        ),
    },
    {
        "name": "rds-postgres",
        "mess": None,
        "resources": [
            ("aws_db_instance", "this"),
            ("aws_db_subnet_group", "this"),
        ],
        "variables": [
            {"name": "identifier", "description": "RDS instance identifier.", "type": "string"},
            {"name": "engine_version", "description": "PostgreSQL engine version.", "type": "string", "default": "15.4"},
            {"name": "instance_class", "description": "RDS instance class.", "type": "string", "default": "db.t3.micro"},
            {"name": "allocated_storage", "description": "Storage in GB.", "type": "number", "default": 20},
            {"name": "username", "description": "Master username.", "type": "string"},
            {"name": "password", "description": "Master password.", "type": "string"},
            {"name": "backup_retention_period", "description": "Days to retain automated backups.", "type": "number", "default": 7},
            {"name": "multi_az", "description": "Enable Multi-AZ deployment.", "type": "bool", "default": False},
        ],
        "outputs": [
            {"name": "endpoint", "description": "Connection endpoint.", "value": "aws_db_instance.this.endpoint"},
            {"name": "address", "description": "Hostname.", "value": "aws_db_instance.this.address"},
            {"name": "port", "description": "Port.", "value": "aws_db_instance.this.port"},
        ],
        "readme": (
            "# rds-postgres\n\nManaged PostgreSQL database on RDS with "
            "automated backups, subnet group, and optional Multi-AZ. Use it "
            "when an application needs a relational database without running "
            "your own servers.\n"
        ),
    },

    # ---- MESSY modules ------------------------------------------------------
    {
        "name": "vpc-networking",
        "mess": "MISLEADING NAME: directory suggests VPC/subnets, but the module provisions security groups.",
        "resources": [
            ("aws_security_group", "this"),
            ("aws_security_group_rule", "allow_ssh"),
        ],
        "variables": [
            {"name": "vpc_id", "description": "ID of the VPC.", "type": "string"},
            {"name": "sg_name", "description": "Name for the security group.", "type": "string"},
            {"name": "allowed_ssh_cidrs", "type": "list(string)", "default": []},
            {"name": "project", "type": "string", "default": "default"},
        ],
        "outputs": [
            {"name": "security_group_id", "value": "aws_security_group.this.id"},
        ],
        "readme": (
            "# vpc-networking\n\nNetworking setup for the VPC. Creates the "
            "required network access controls for workloads in the VPC.\n"
        ),
    },
    {
        "name": "fw-rules-legacy",
        "mess": "MISSING DESCRIPTIONS + terse README. Overlaps sg-web-firewall / vpc-networking.",
        "resources": [
            ("aws_security_group", "this"),
        ],
        "variables": [
            {"name": "name", "type": "string"},
            {"name": "vpc_id", "type": "string"},
            {"name": "ingress_ports", "description": "List of ports to open.", "type": "list(number)", "default": [22]},
            {"name": "cidr_blocks", "type": "list(string)", "default": ["0.0.0.0/0"]},
        ],
        "outputs": [
            {"name": "id", "value": "aws_security_group.this.id"},
        ],
        "readme": "Legacy firewall rules. See wiki.\n",
    },
    {
        "name": "team-storage",
        "mess": "COPY-PASTE README: describes an RDS/PostgreSQL database, but the module provisions S3 buckets.",
        "resources": [
            ("aws_s3_bucket", "this"),
            ("aws_s3_bucket_versioning", "this"),
        ],
        "variables": [
            {"name": "bucket_name", "description": "Name of the bucket.", "type": "string"},
            {"name": "team", "description": "Team that owns this storage.", "type": "string"},
            {"name": "versioning", "type": "bool", "default": True},
        ],
        "outputs": [
            {"name": "bucket_arn", "description": "Bucket ARN.", "value": "aws_s3_bucket.this.arn"},
        ],
        "readme": (
            "# team-storage\n\nProvisions a PostgreSQL RDS database with "
            "automated backups and Multi-AZ support for the team. Includes "
            "parameter group tuning for production workloads.\n"
        ),
    },
    {
        "name": "s3-stuff",
        "mess": "UNHELPFUL NAMES + no descriptions + no README at all.",
        "resources": [
            ("aws_s3_bucket", "this"),
        ],
        "variables": [
            {"name": "x", "type": "string"},
            {"name": "thing", "type": "bool", "default": False},
            {"name": "bucket", "type": "string", "default": ""},
        ],
        "outputs": [
            {"name": "out1", "value": "aws_s3_bucket.this.id"},
        ],
        "readme": None,
    },
    {
        "name": "data-store",
        "mess": "MISLEADING NAME: sounds like object storage, but provisions an RDS PostgreSQL database.",
        "resources": [
            ("aws_db_instance", "this"),
        ],
        "variables": [
            {"name": "name", "description": "Name of the data store.", "type": "string"},
            {"name": "instance_class", "type": "string", "default": "db.t3.small"},
            {"name": "storage_gb", "description": "Storage in GB.", "type": "number", "default": 50},
            {"name": "password", "type": "string"},
        ],
        "outputs": [
            {"name": "endpoint", "description": "DB endpoint.", "value": "aws_db_instance.this.endpoint"},
        ],
        "readme": "Shared data store for the team.\n",
    },
    {
        "name": "mysql-thing",
        "mess": "SPARSE: almost no descriptions, one-line README. Overlaps rds-postgres / data-store.",
        "resources": [
            ("aws_db_instance", "this"),
        ],
        "variables": [
            {"name": "identifier", "type": "string"},
            {"name": "instance_class", "type": "string", "default": "db.t3.micro"},
            {"name": "allocated_storage", "type": "number", "default": 20},
        ],
        "outputs": [
            {"name": "endpoint", "value": "aws_db_instance.this.endpoint"},
        ],
        "readme": "mysql db\n",
    },
    {
        "name": "dns-zones",
        "mess": "MISSING DESCRIPTIONS, minimal README.",
        "resources": [
            ("aws_route53_zone", "this"),
            ("aws_route53_record", "www"),
        ],
        "variables": [
            {"name": "domain_name", "type": "string"},
            {"name": "ttl", "type": "number", "default": 300},
            {"name": "record_type", "description": "DNS record type, e.g. A.", "type": "string", "default": "A"},
        ],
        "outputs": [
            {"name": "zone_id", "description": "Route53 zone ID.", "value": "aws_route53_zone.this.zone_id"},
            {"name": "name_servers", "description": "Name servers.", "value": "aws_route53_zone.this.name_servers"},
        ],
        "readme": "Route53 zones.\n",
    },
    {
        "name": "logs-bucket",
        "mess": "OVERLAP: an S3 bucket like s3-private-bucket, but specialized for access logs.",
        "resources": [
            ("aws_s3_bucket", "this"),
            ("aws_s3_bucket_lifecycle_configuration", "this"),
        ],
        "variables": [
            {"name": "bucket_name", "description": "Bucket that receives logs.", "type": "string"},
            {"name": "log_retention_days", "description": "Days to keep logs before expiry.", "type": "number", "default": 90},
            {"name": "source", "description": "Where logs come from, e.g. alb.", "type": "string", "default": "alb"},
        ],
        "outputs": [
            {"name": "id", "description": "Bucket name.", "value": "aws_s3_bucket.this.id"},
            {"name": "arn", "description": "Bucket ARN.", "value": "aws_s3_bucket.this.arn"},
        ],
        "readme": (
            "# logs-bucket\n\nCentral S3 bucket for access logs (ALB/NLB, "
            "CloudFront) with lifecycle expiry. Point your log delivery here "
            "instead of mixing logs into application buckets.\n"
        ),
    },
    {
        "name": "old-logging",
        "mess": "COPY-PASTE README: describes firewall rules for EC2, but the module provisions an S3 log bucket.",
        "resources": [
            ("aws_s3_bucket", "this"),
        ],
        "variables": [
            {"name": "bucket", "type": "string"},
            {"name": "retention", "type": "number", "default": 30},
        ],
        "outputs": [
            {"name": "arn", "value": "aws_s3_bucket.this.arn"},
        ],
        "readme": (
            "# old-logging\n\nManages firewall rules for EC2 instances. "
            "Configures ingress and egress rules to control traffic to your "
            "servers.\n"
        ),
    },
    {
        "name": "notify-slack",
        "mess": "SPARSE but honest.",
        "resources": [
            ("aws_sns_topic", "this"),
        ],
        "variables": [
            {"name": "topic_name", "description": "Name of the SNS topic.", "type": "string"},
            {"name": "slack_webhook_url", "type": "string"},
        ],
        "outputs": [
            {"name": "topic_arn", "description": "SNS topic ARN.", "value": "aws_sns_topic.this.topic_arn"},
        ],
        "readme": "SNS topic wired to a Slack webhook for alerts.\n",
    },
    {
        "name": "ec2-webserver",
        "mess": "MISSING DESCRIPTIONS.",
        "resources": [
            ("aws_instance", "this"),
            ("aws_eip", "this"),
        ],
        "variables": [
            {"name": "ami_id", "type": "string"},
            {"name": "instance_type", "type": "string", "default": "t3.micro"},
            {"name": "key_name", "description": "SSH key pair name.", "type": "string"},
            {"name": "subnet_id", "type": "string"},
        ],
        "outputs": [
            {"name": "public_ip", "description": "Public IP.", "value": "aws_eip.this.public_ip"},
            {"name": "instance_id", "description": "Instance ID.", "value": "aws_instance.this.id"},
        ],
        "readme": "EC2 web server.\n",
    },
    {
        "name": "cache-redis",
        "mess": "SPARSE. Overlaps the database family (also a data store, different engine).",
        "resources": [
            ("aws_elasticache_cluster", "this"),
        ],
        "variables": [
            {"name": "cluster_id", "description": "Redis cluster ID.", "type": "string"},
            {"name": "node_type", "type": "string", "default": "cache.t3.micro"},
            {"name": "num_nodes", "type": "number", "default": 1},
        ],
        "outputs": [
            {"name": "cache_endpoint", "description": "Redis endpoint.", "value": "aws_elasticache_cluster.this.cache_nodes[0].address"},
            {"name": "port", "description": "Redis port.", "value": "aws_elasticache_cluster.this.cache_nodes[0].port"},
        ],
        "readme": "Redis cache.\n",
    },
]


def write_module(spec: dict) -> None:
    mod_dir = OUT_DIR / spec["name"]
    mod_dir.mkdir(parents=True, exist_ok=True)
    (mod_dir / "variables.tf").write_text(render_variables(spec["variables"]))
    (mod_dir / "outputs.tf").write_text(render_outputs(spec["outputs"]))
    (mod_dir / "main.tf").write_text(render_main(spec["resources"]))
    if spec.get("readme"):
        (mod_dir / "README.md").write_text(spec["readme"])
    # Document the intended pathology next to the module for humans.
    notes = [f"# {spec['name']}", ""]
    notes.append(f"Intended pathology: {spec['mess']}" if spec["mess"] else "Intended pathology: none (clean reference module).")
    (mod_dir / "EVAL_NOTES.md").write_text("\n".join(notes) + "\n")


def main() -> None:
    if OUT_DIR.exists():
        for child in OUT_DIR.iterdir():
            if child.is_dir():
                for f in sorted(child.rglob("*")):
                    if f.is_file():
                        f.unlink()
                child.rmdir()
            else:
                child.unlink()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for spec in MODULES:
        write_module(spec)

    n_messy = sum(1 for m in MODULES if m["mess"])
    print(f"Wrote {len(MODULES)} modules ({n_messy} messy, {len(MODULES) - n_messy} clean) to {OUT_DIR}")


if __name__ == "__main__":
    main()
