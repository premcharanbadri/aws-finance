# Bedrock + AgentCore hackathon starter

Run these commands **in your workshop terminal**, from the folder containing this kit. Use the AWS credentials supplied for your participant account; no Bedrock API key is needed.

## 1. Check the environment

```bash
export AWS_REGION=us-east-1  # Change this if your workshop uses another region.
aws sts get-caller-identity
python3 --version
```

The account and role printed here tell you which AWS permissions the terminal is using. The default model is GPT-5.6 Luna through the regional inference profile `us.openai.gpt-5.6-luna`. Your participant role needs permission to invoke that profile and its routed foundation models.

## 2. Run direct Bedrock inference

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python infer.py
```

Expected response: `OK` or `OK.`. This proves that the **terminal role** can call GPT-5.6 Luna. Change the prompt with:

```bash
.venv/bin/python infer.py "Say hello in one sentence."
```

To choose another tested GPT profile, set `BEDROCK_MODEL_ID` before running either script:

| Model | `BEDROCK_MODEL_ID` |
| --- | --- |
| GPT-5.4 | `us.openai.gpt-5.4` |
| GPT-5.5 | `us.openai.gpt-5.5` |
| GPT-5.6 Luna | `us.openai.gpt-5.6-luna` |
| GPT-5.6 Sol | `us.openai.gpt-5.6-sol` |
| GPT-5.6 Terra | `us.openai.gpt-5.6-terra` |

For example: `export BEDROCK_MODEL_ID=us.openai.gpt-5.6-sol`. Use the **profile ID** with Bedrock Converse; the base `openai.gpt-5.6-*` IDs may not support on-demand throughput. Verify profile availability in your assigned region with `aws bedrock list-inference-profiles --region "$AWS_REGION"`. A Bedrock API key does not override an IAM explicit deny.

## 3. Create your AgentCore role

The kit creates an IAM role named **`hackathon-gpt-agentcore-execution-role`** in your own account. Its policy covers the selected inference profile and the foundation models listed by Bedrock for that profile. Your participant credentials need `iam:CreateRole`, `iam:PutRolePolicy`, and `iam:PassRole` for this role. If your account does not grant those actions, AWS will return `AccessDenied`; the script cannot grant permissions to itself.

```bash
export BEDROCK_MODEL_ID=${BEDROCK_MODEL_ID:-us.openai.gpt-5.6-luna}
export AGENTCORE_EXECUTION_ROLE_ARN=$(.venv/bin/python setup_agentcore_role.py)
echo "$AGENTCORE_EXECUTION_ROLE_ARN"
```

If another participant shares your AWS account, set a unique `AGENTCORE_ROLE_NAME` before running the setup script. The script creates a new role; rerunning it with the same name returns an IAM `EntityAlreadyExists` error.

## 4. Deploy an AgentCore Runtime

Your participant credentials also need permission to create and invoke AgentCore runtimes and create or use the S3 code bucket required by direct code deployment. Do not put credentials or API keys in `agent.py`.

```bash
.venv/bin/agentcore configure \
  --entrypoint agent.py \
  --name workshop_gpt_agent \
  --region "$AWS_REGION" \
  --deployment-type direct_code_deploy \
  --requirements-file requirements.txt \
  --execution-role "$AGENTCORE_EXECUTION_ROLE_ARN" \
  --disable-otel \
  --disable-memory \
  --non-interactive
.venv/bin/agentcore deploy --agent workshop_gpt_agent \
  --env "BEDROCK_MODEL_ID=$BEDROCK_MODEL_ID"
```

Wait for **Deployment Success** and save the runtime ARN printed by the CLI. The runtime ARN looks like `arn:aws:bedrock-agentcore:REGION:ACCOUNT:runtime/NAME-ID`.

```bash
.venv/bin/agentcore invoke '{"prompt":"Reply with OK."}' \
  --agent workshop_gpt_agent
```

Expected response: `{"model":"us.openai.gpt-5.6-luna","text":"OK"}` (punctuation may vary). This proves that the **deployed runtime role** can invoke the model. The CLI may add session and request IDs around the response.

The Python handler accepts `{"prompt":"..."}` and returns `{"model":"...","text":"..."}`. Replace the handler logic with your hackathon project after this baseline works.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `AccessDenied` on `InvokeModel` | Model access, region, inference profile ID, workshop allowlist, and the caller role. |
| Direct inference works, AgentCore inference fails | AgentCore execution role trust and GPT profile plus routed model permissions. The two calls use different roles. |
| On-demand throughput is not supported | Use the `us.openai.*` inference profile ID, not the base `openai.*` model ID. |
| IAM role creation or `iam:PassRole` denied | Your participant role lacks the required IAM action. Check the account's permission policy; the script cannot override an explicit deny. |
| Runtime deployment never becomes ready | Inspect the runtime's CloudWatch logs and verify the entrypoint and package dependencies. |
| Local server says port 8080 is busy | Skip the local server test or use another free port; cloud deployment uses the standard runtime port. |

Each inference and active AgentCore runtime can incur AWS charges. Remove the runtime and its dedicated role when you finish, following your event's cleanup procedure.
