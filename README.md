# AI Governance Dashboard

> AI Observability & Governance Dashboard for Enterprise AI Systems

## Architecture

![Architecture Diagram](architecture/architecture-diagram.png)

<!-- TODO: Replace with actual architecture diagram from excalidraw.com -->

## Overview

A comprehensive observability and governance platform for monitoring enterprise AI systems in production. Tracks model performance, token usage, cost attribution, prompt/response quality, and policy compliance across multiple LLM deployments — providing a single pane of glass for AI operations teams.

### Key Features

- **LLM Observability**: Real-time monitoring of latency, token usage, error rates, and cost per model
- **Prompt Analytics**: Track prompt templates, A/B test performance, and detect prompt drift
- **Guardrails Monitoring**: Validate outputs against content policies and compliance rules
- **Cost Attribution**: Per-team, per-project cost tracking and budget alerting
- **Audit Trail**: Immutable logging of all AI interactions for regulatory compliance
- **Dashboard**: Interactive Streamlit dashboard with drill-down analytics

## Tech Stack

| Layer | Technology |
|-------|-----------|
| LLM Providers | AWS Bedrock, OpenAI |
| Tracing | LangSmith, OpenTelemetry |
| Data Pipeline | Databricks (Delta Lake) |
| Backend | FastAPI |
| Frontend | Streamlit / Gradio |
| Guardrails | Guardrails AI, custom validators |
| Monitoring | Prometheus, Grafana |

## Setup

### Prerequisites

- Python 3.11+
- Databricks workspace
- LangSmith account
- AWS account with Bedrock access

### Installation

```bash
git clone https://github.com/gyaneshwarmalba/ai-governance-dashboard.git
cd ai-governance-dashboard
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Fill in your credentials
```

### Running

```bash
streamlit run app/dashboard.py
```

## Demo

<!-- TODO: Add demo GIF/video -->

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
