# Boardroom AI

기업 고객을 위한 네 역할의 재무 연구 데모입니다. Marketer → CPA → Portfolio → Reviewer → 최대 한 번 수정 → 백테스트 → Streamlit 화면까지 연결되어 있습니다.

## 1 바로 실행

압축을 풀고 이 README가 있는 폴더에서 실행하세요. Python 3.11 이상을 권장합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

행사 VS Code에서 port 8501 미리보기/포트 포워딩으로 접속합니다. Mac의 localhost와 원격 행사 서버는 다릅니다. 프록시 경로가 안 보이면 행사 멘토에게 IDE preview 경로를 확인하세요.

처음에는 Rehearsal 모드와 합성 가격이 선택되어 있습니다. `Run advisory team`을 누르면 AWS 인증 없이 화면을 확인할 수 있습니다. 합성 데이터와 규칙 기반 출력이라는 경고가 항상 표시됩니다. 이것을 실제 투자 성과나 AI 분석으로 발표하면 안 됩니다.

## 2 실제 AWS 모델 연결

기존 행사 AWS 역할 인증을 유지하세요. AWS 키를 코드나 GitHub에 넣지 않습니다.

```bash
export AWS_REGION='YOUR_WORKSHOP_REGION'
export BEDROCK_MODEL_ID='YOUR_WORKING_MODEL_OR_INFERENCE_PROFILE_ID'
python scripts/smoke_test.py
```

위 두 문자열은 예시가 아니라 교체해야 하는 자리 표시자입니다. 행사 `infer.py`에서 성공한 리전과 모델 ID를 그대로 사용하세요. 또는 `.env.example`을 `.env`로 복사해 설정합니다. `.env`는 Git에서 제외됩니다.

연결이 확인되면 UI에서 `Amazon Bedrock — live model calls`를 선택합니다. 각 역할은 실제 Bedrock Converse 호출을 수행합니다. 모델 오류가 나도 리허설로 자동 전환하지 않습니다. 모델별 매개변수 호환성을 위해 temperature는 기본으로 보내지 않습니다.

이 버전은 빠른 시연을 위한 명시적인 Python 오케스트레이션과 역할별 LLM 호출입니다. 계산은 Python이 수행합니다. 모델이 도구를 자율 선택하는 agent loop, Strands 런타임, AgentCore 배포를 구현했다고 주장하지 마세요. 현재 실행에는 필요하지 않습니다.

## 3 실제 데이터 넣기

필수 데이터는 가격 CSV 하나입니다. 가장 빠른 경로는 제공받은 Kaggle S&P 500 자료를 다운로드해 전체 가격 CSV를 UI에 올리거나 `data/prices.csv`에 저장하는 것입니다. CSV에 실제 존재하는 6개 종목을 고르세요. 형식과 추가 재무 근거는 `DATA_GUIDE.md`를 보세요.

UI에서 `Use bundled synthetic prices`를 해제해야 실제 가격 파일을 사용합니다. 파일 기간 안에서 cutoff와 평가 종료일을 고정하세요. 가격 업로드만으로도 전체 흐름이 작동하지만, SEC·시장 데이터가 없다는 한계는 그대로 표시됩니다.

선택 다운로드 스크립트:

```bash
python scripts/download_prices.py --tickers AAPL MSFT AMZN JPM XOM JNJ --start 20160101 --end 20171231
```

Stooq의 네트워크·브라우저 검증 때문에 실패할 수 있습니다. 이 다운로드의 실제 성공은 제공 환경에서 검증하지 못했습니다. 실패하면 브라우저에서 CSV를 받아 업로드하세요. 오류 시 합성 데이터를 몰래 대체하지 않습니다.

## 4 폴더와 팀 역할

```text
app.py                   Streamlit 화면
orchestrator.py          역할 호출, 수정 한 번, 승인 및 평가
contracts.py             공통 Pydantic 스키마
agents/marketer.py        시장·사업 맥락
agents/cpa.py             현금 계산과 회계 검토 질문
agents/portfolio.py       네 담당인 포트폴리오 생성·수정
agents/reviewer.py        네 담당인 독립 검토
services/llm.py           공통 Bedrock 연결과 JSON 스키마 검사
services/data.py          CSV 로딩, 기준일 필터, 가격 지표
services/validation.py    비중·종목·근거·수정 응답 검사
services/backtest.py      고정 수량 백테스트와 기준선 비교
services/simulation.py    명시적 리허설 규칙
scripts/                 연결 테스트, 데이터 다운로드, 리허설
tests/                   계산과 실행 흐름 검증
data/                    합성 가격, 실제 가격, 선택 근거
runs/                    실행별 결정 기록
```

각자 자기 에이전트의 프롬프트와 로직을 수정하되 함수 시그니처와 반환 키는 유지하세요.

```python
marketer.run(context, mode='bedrock')
cpa.run(context, mode='bedrock')
portfolio.run(context, marketing, accounting, previous=None, review=None, mode='bedrock')
reviewer.run(context, marketing, accounting, proposal, validation_errors, mode='bedrock')
```

반환값은 dict이며 contracts.py의 스키마를 따릅니다. agents 파일에서 Streamlit을 import하지 마세요. 통합 담당자는 orchestrator.py와 app.py만 주로 관리하면 됩니다. 추가 에이전트용 API 서버는 필요 없습니다.

Portfolio와 Reviewer의 리허설 출력도 기본 구현되어 있으므로 팀원 작업을 기다리지 않고 통합 실행할 수 있습니다. Reviewer는 항상 반대할 필요가 없으며, 실제 근거가 없으면 accept가 가능합니다. 실제 모델 모드에서 수정이 반드시 일어난다고 보장하지 않습니다.

## 5 검증과 백업

```bash
python -m unittest discover -s tests -v
python scripts/run_demo.py
```

테스트는 AWS를 호출하지 않습니다. 리허설 결과는 runs에 저장됩니다. 화면에서도 전체 JSON과 곡선 CSV를 다운로드할 수 있습니다. 실행 ID, 입력, 근거, 모델 ID, 가격 SHA256, 지표, 가정을 보관합니다. 동일 모델 호출의 재현성은 보장하지 않으므로 발표에 사용한 기록과 원본 가격 파일을 함께 보관하세요.

## 6 해석과 제한

* 기준일 종가까지 분석하고 다음 공통 거래일 종가로 진입합니다. 주식 수량은 이후 고정입니다.
* 동일 비중, 최초안, 검토안을 같은 기간·예산·진입 비용으로 평가합니다. 잘못된 최초안은 성과 비교에서 제외됩니다.
* 수정 후에도 정책 오류나 미해결 주요 검토가 남으면 escalated 상태로 종료하며 승인 성과를 만들지 않습니다.
* 보유 가격의 배당·분할 처리 방법을 확인하세요. 배당은 별도로 더하지 않습니다. 데이터 관례를 모르면 total return이라고 표시하지 마세요.
* 가격 누락일은 선택 종목 모두에 공통인 날짜만 남기며 몇 개가 제외됐는지 표시합니다. 미래 가격으로 채우지 않습니다.
* 약정 자금·운영 예비금은 주식 투자에서 제외됩니다. 총자본 그래프는 미투자 현금에 수익 0을 가정한 참고 화면입니다.
* 그룹 노출은 client가 입력한 시나리오 가정이며 외부 검증된 산업 노출이 아닙니다. 시장 성장·매출 증가·절세 효과를 백테스트로 검증했다고 주장하지 마세요.
* 분석가는 평가 가격을 받지 않지만 현대 LLM의 사후 사건 기억과 현재 생존 종목 선택 편향은 남을 수 있습니다. 과거 자료에 제한된 회고 실험입니다.
* 실제 고객 자문·회계 감사·세금 신고·주문 체결은 수행하지 않습니다.

## 7 GitHub 프로젝트에 통합

이 패키지는 독립된 완성 프로젝트입니다. 팀 저장소에 복사하기 전에 작업 브랜치를 만들고, 이미 팀원이 작성한 파일과 변경 내용을 비교하세요. 같은 이름의 파일을 무조건 덮어쓰지 마세요. 통합 담당자 한 명이 코드 기준선을 정하고 필요한 파일을 합치는 것이 빠릅니다.

## 8 AgentCore

현재 프로젝트는 행사 IDE의 Streamlit 서버에서 실행됩니다. AgentCore 배포는 포함되지 않았습니다. 필수라면 주최 측 스타터를 유지하고 handler에서 orchestrator.run을 호출하도록 연결하세요. 가격 파일은 S3 등 런타임에서 접근 가능한 위치에 따로 제공해야 합니다. Streamlit UI가 AgentCore에 자동으로 호스팅되는 것은 아닙니다. 실제 스타터 내용을 받지 않은 상태에서 배포 명령이나 권한 설정을 가정하지 않았습니다.

## 9 3분 발표

1. 고객 시나리오, 종목, 기준일, 동일 비중 기준선을 말합니다.
2. 회계 검토가 투자 가능 금액을 정하는 모습을 보여줍니다.
3. 최초 배분, Reviewer 근거, 실제 수정 응답을 보여줍니다.
4. 실제 가격으로 평가한 곡선과 최대 낙폭을 보여줍니다. 성과가 나쁘면 그대로 설명합니다.
5. 결정 기록을 내보내고 구독형 기업 연구 도구라는 제품 가설을 설명합니다.
