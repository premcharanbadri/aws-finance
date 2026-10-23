"""Minimal hackathon data stack: S3 bucket + Redshift Serverless in the default VPC.

Deliberately simple: default VPC, one IAM role, publicly accessible workgroup.
Not intended for production.
"""
import boto3
from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_redshiftserverless as rss,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

PREFIX = "aws-finance-hackathon"

# AZ IDs that Redshift Serverless does NOT support in us-east-1. Subnets in these
# AZs are skipped so workgroup creation doesn't fail.
UNSUPPORTED_AZ_IDS = {"use1-az3"}


def default_vpc_subnets(region: str, count: int = 3) -> list:
    """Return up to `count` default-VPC subnet IDs in Redshift-supported AZs.

    Looked up at synth time with boto3 (simpler than CDK context lookups).
    """
    ec2_client = boto3.client("ec2", region_name=region)
    subnets = ec2_client.describe_subnets(
        Filters=[{"Name": "default-for-az", "Values": ["true"]}]
    )["Subnets"]
    usable = sorted(
        (s for s in subnets if s["AvailabilityZoneId"] not in UNSUPPORTED_AZ_IDS),
        key=lambda s: s["AvailabilityZone"],
    )
    if len(usable) < 2:
        raise RuntimeError("Need at least 2 default subnets in supported AZs")
    return [s["SubnetId"] for s in usable[:count]]


class AwsFinanceHackathonStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        bucket_name = self.node.try_get_context("bucket_name") or f"{PREFIX}-bucket"
        allowed_cidr = self.node.try_get_context("allowed_cidr") or "0.0.0.0/0"
        db_name = "dev"

        # ---- S3: raw CSV landing bucket (destroyed with contents on teardown) ----
        bucket = s3.Bucket(
            self,
            "RawBucket",
            bucket_name=bucket_name,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
        )

        # ---- IAM role Redshift assumes for COPY from S3 ----
        redshift_role = iam.Role(
            self,
            "RedshiftS3Role",
            role_name=f"{PREFIX}-redshift-s3-role",
            assumed_by=iam.CompositePrincipal(
                iam.ServicePrincipal("redshift.amazonaws.com"),
                iam.ServicePrincipal("redshift-serverless.amazonaws.com"),
            ),
        )
        bucket.grant_read(redshift_role)

        # ---- Networking: default VPC + one SG opening 5439 ----
        vpc = ec2.Vpc.from_lookup(self, "DefaultVpc", is_default=True)
        sg = ec2.SecurityGroup(
            self,
            "RedshiftSg",
            vpc=vpc,
            security_group_name=f"{PREFIX}-redshift-sg",
            description="aws-finance-hackathon: Redshift Serverless access on 5439",
            allow_all_outbound=True,
        )
        sg.add_ingress_rule(
            ec2.Peer.ipv4(allowed_cidr), ec2.Port.tcp(5439), "Redshift from laptop"
        )

        # ---- Admin password generated into Secrets Manager ----
        # JSON {"username": "admin", "password": "..."} — the shape the Data API expects.
        admin_secret = secretsmanager.Secret(
            self,
            "AdminSecret",
            secret_name=f"{PREFIX}-redshift-admin",
            removal_policy=RemovalPolicy.DESTROY,
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username": "admin"}',
                generate_string_key="password",
                password_length=32,
                # Redshift disallows / @ " ' \ and spaces in passwords
                exclude_characters="/@\"'\\ `",
            ),
        )

        # ---- Redshift Serverless ----
        namespace = rss.CfnNamespace(
            self,
            "Namespace",
            namespace_name=PREFIX,
            db_name=db_name,
            admin_username="admin",
            admin_user_password=admin_secret.secret_value_from_json("password").unsafe_unwrap(),
            iam_roles=[redshift_role.role_arn],
            default_iam_role_arn=redshift_role.role_arn,
        )

        workgroup = rss.CfnWorkgroup(
            self,
            "Workgroup",
            workgroup_name=PREFIX,
            namespace_name=PREFIX,
            base_capacity=4,  # lowest base RPU
            publicly_accessible=True,
            subnet_ids=default_vpc_subnets(self.region),
            security_group_ids=[sg.security_group_id],
        )
        workgroup.add_resource_dependency(namespace)

        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "WorkgroupName", value=PREFIX)
        CfnOutput(self, "NamespaceName", value=PREFIX)
        CfnOutput(self, "Database", value=db_name)
        CfnOutput(self, "AdminSecretArn", value=admin_secret.secret_arn)
        CfnOutput(self, "RedshiftRoleArn", value=redshift_role.role_arn)
        CfnOutput(self, "WorkgroupEndpoint", value=workgroup.attr_workgroup_endpoint_address)
