"""Direct Bedrock Converse check using the workshop's AWS role."""

import argparse
import os

import boto3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="?", default="Reply with OK.")
    args = parser.parse_args()
    model_id = os.getenv("BEDROCK_MODEL_ID", "us.openai.gpt-5.6-luna")
    region = os.getenv("AWS_REGION", "us-east-1")
    response = boto3.client("bedrock-runtime", region_name=region).converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": args.prompt}]}],
        inferenceConfig={"maxTokens": 128, "temperature": 0},
    )
    print("".join(part.get("text", "") for part in response["output"]["message"]["content"]))


if __name__ == "__main__":
    main()
