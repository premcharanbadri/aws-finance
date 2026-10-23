"""Create a GPT AgentCore execution role in the caller's AWS account."""

import json
import os
import sys

import boto3
from botocore.exceptions import ClientError


def main():
    region = os.getenv("AWS_REGION", "us-east-1")
    profile_id = os.getenv("BEDROCK_MODEL_ID", "us.openai.gpt-5.6-luna")
    role_name = os.getenv("AGENTCORE_ROLE_NAME", "hackathon-gpt-agentcore-execution-role")
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    profile = boto3.client("bedrock", region_name=region).get_inference_profile(
        inferenceProfileIdentifier=profile_id
    )
    resources = [profile["inferenceProfileArn"]]
    resources.extend(model["modelArn"] for model in profile["models"])
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {"StringEquals": {"aws:SourceAccount": account_id}},
        }],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            "Resource": resources,
        }],
    }
    iam = boto3.client("iam")
    role = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(trust),
        Description=f"Hackathon AgentCore access to {profile_id}",
    )["Role"]
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="gpt-inference-profile",
        PolicyDocument=json.dumps(policy),
    )
    print(role["Arn"])


if __name__ == "__main__":
    try:
        main()
    except ClientError as exc:
        print(f"Role setup failed: {exc}", file=sys.stderr)
        sys.exit(1)
