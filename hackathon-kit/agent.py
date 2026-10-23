"""Minimal AgentCore Runtime that calls a GPT model through Bedrock."""

import os

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()
DEFAULT_MODEL_ID = "us.openai.gpt-5.6-luna"


@app.entrypoint
def invoke(payload):
    prompt = payload.get("prompt", "Reply with OK.")
    model_id = os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    response = boto3.client(
        "bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1")
    ).converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 128, "temperature": 0},
    )
    text = "".join(
        part.get("text", "") for part in response["output"]["message"]["content"]
    )
    return {"model": model_id, "text": text}


if __name__ == "__main__":
    app.run()
