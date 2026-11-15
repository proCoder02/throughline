# Self-hosted model for Corporate's background work

A small model (Qwen2.5-3B), fine-tuned for free on Kaggle and run for free on
Oracle, takes over Corporate's background AI calls (evidence check, risk
radar, chat extraction). Anything a person waits on stays on the cloud model.
Diagram and reasoning: the "Free Model Pipeline" doc.

## Steps

| # | What | Where | Command |
|---|---|---|---|
| 1 | Collect examples (synthetic, no user data) | this machine | `python ml/make_synthetic.py --n 150` |
| 2 | Clean and split 90/10 | this machine | `python ml/clean.py` |
| 3–4 | Fine-tune, score before/after, export GGUF | Kaggle | upload `ml/data/train.jsonl` + `test.jsonl` as a private dataset, open `ml/finetune_kaggle.ipynb`, GPU T4 x2, Run all |
| 5 | Run it with Ollama | Oracle A1 server | `sudo bash ml/deploy/setup_ollama.sh throughline-3b-Q4_K_M.gguf <private-ip> <app-private-ip> [same-host]` |
| 6 | Test gate | app server | `python ml/check_model.py http://<private-ip>:11434 throughline-3b` (needs ≥95% valid JSON, ≥85% agreement) |
| 7 | Switch on | app server `.env` | `LOCAL_LLM_URL=http://<private-ip>:11434`, restart. Unset to roll back. |

## How the app chooses a model (`corporate/llm.py`)

- People-facing (Ask, common ground, meetings): Ollama Cloud, then **Groq** if it fails (out of credit, rate limit, down).
- Background jobs: the self-hosted model if `LOCAL_LLM_URL` is set, then the people-facing chain.

## Collecting from the app itself

`CORPORATE_COLLECT_DIR=ml/data/raw` makes `corporate/ai.py` save every good AI answer as a training example.
Only turn it on with test organisations; real company data needs their written permission to be used for training.
`ml/data/` is git-ignored.
