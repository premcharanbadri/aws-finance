#!/usr/bin/env bash
# Launch the Streamlit UI fully detached so it survives the parent shell.
set -euo pipefail
cd "$(dirname "$0")"

export DATA_PROVIDER="${DATA_PROVIDER:-yfinance}"
export SENTIMENT_PROVIDER="${SENTIMENT_PROVIDER:-comprehend}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
PORT="${PORT:-8501}"
# Served behind the environment's CloudFront /proxy/<port>/ reverse proxy,
# so Streamlit must know its base URL path for links/websockets to resolve.
BASE_URL_PATH="${BASE_URL_PATH:-proxy/${PORT}}"

pkill -f "streamlit run app.py" 2>/dev/null || true
sleep 1

# Double-detached: setsid + nohup + disown, stdio fully redirected to a file.
setsid nohup .venv/bin/streamlit run app.py \
  --server.address 0.0.0.0 \
  --server.port "$PORT" \
  --server.baseUrlPath "$BASE_URL_PATH" \
  --server.headless true \
  --server.enableCORS false \
  --server.enableXsrfProtection false \
  --browser.gatherUsageStats false \
  </dev/null >/tmp/streamlit.log 2>&1 &
disown || true
echo "streamlit launching on port $PORT, baseUrlPath=/$BASE_URL_PATH (logs: /tmp/streamlit.log)"
