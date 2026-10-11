# AI DataOps & LLM Evaluation Platform

A Streamlit application connecting customer-support data annotation, human approval, reference-answer creation, model inference, and response evaluation in one workflow.

The project demonstrates how a team can define expected answers before evaluating AI responses, retain review decisions, and prepare approved examples for downstream training.

**Status:** independent prototype. The current checkout requires the startup fixes below. Training-example export is implemented; a functioning fine-tuning interface is not present in the committed code.

## Workflow

1. **Ingest:** upload a CSV and validate required fields, empty values and duplicate IDs.
2. **Annotate:** assign intent, sentiment, escalation status and an expected support response.
3. **Review:** approve or reject the annotation. Rejection requires comments.
4. **Build a reference set:** approved annotations populate the application's “Gold Dataset.” Approval is a workflow decision, not proof that an answer is objectively correct.
5. **Generate:** run the configured OpenAI model on approved customer messages and record its response, latency, token usage and errors.
6. **Evaluate:** assess responses through human review and DeepEval independently.
7. **Export:** download approved message/answer pairs as chat-format `training.jsonl`.

Rejected annotations do not enter the reference set or inference queue. The current application does not implement a resubmission workflow for rejected annotations.

## What the project demonstrates

- Human oversight before reference answers enter the evaluation workflow.
- Separation of annotation QA from evaluation of generated answers.
- SQL-backed records linking samples, annotations, reviews, inference attempts and evaluations.
- Explicit evaluation criteria and a configurable implementation that can be extended to other support workflows.
- Training-data preparation from reviewer-approved answers rather than unreviewed model outputs.

## Input

Upload a CSV with these required columns:

| Column | Meaning |
|---|---|
| `sample_id` | Unique item identifier |
| `customer_message` | Customer-support message to annotate |

```csv
sample_id,customer_message
demo-001,I cannot access my account.
demo-002,"I was charged twice, please help."
```

A sample file is provided at `data/customer_support_dataset.csv`. Additional CSV columns are not used by the stored sample workflow. Dataset name and description are displayed during ingestion but are not persisted as separate dataset records.

## Evaluation

| Method | Criteria | Decision |
|---|---|---|
| Human review | Correctness, relevance, completeness, helpfulness: each 1–5; hallucination flag; comments | Evaluator selects Pass or Fail; failure requires comments |
| DeepEval GEval | Correctness, relevance, completeness, helpfulness: each 0–1, with reasons | Average score rounded to two decimals; Pass when average is at least 0.80 |

The generator is configured as `gpt-5`. DeepEval's judge model is not explicitly configured in this project and uses its library default. Automated judgments are probabilistic and do not replace a qualified reviewer.

**Important:** metric exceptions currently become score `0.0` with the error as the reason. A failed API call can therefore appear as an evaluation failure. Inspect reasons before interpreting scores.

## Local setup

Use Python 3.10 or newer because the code uses modern type annotations. Dependencies are largely unpinned, so compatibility should be checked in your environment.

```bash
git clone https://github.com/bs5299-PM/AI-Project-Manager.git
cd AI-Project-Manager/AI-DataOps-LLM-Evaluation-Platform
python -m venv .venv
```

Activate on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Activate on macOS/Linux:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

### Required startup fixes in the current checkout

These are instructions for changes to the source; they have not been applied by this README.

**1. Correct the evaluation import in `app.py`.** The service lives in the `evaluation` directory:

```python
from evaluation.deepeval_service import run_deepeval
```

**2. Read credentials from the environment.** In `app.py`, add `import os` and replace the empty key constant with:

```python
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY or "not-configured")
```

The placeholder allows local annotation and review without a key; the existing inference guard rejects an empty key before generating responses. In `evaluation/deepeval_service.py`, remove the assignment that overwrites `os.environ["OPENAI_API_KEY"]` with an empty string. Never insert credentials into committed source.

For API-backed inference and evaluation, set your key in the same terminal before launching:

```powershell
# Windows PowerShell — replace the placeholder locally
$env:OPENAI_API_KEY = "YOUR_API_KEY"
```

```bash
# macOS/Linux — replace the placeholder locally
export OPENAI_API_KEY="YOUR_API_KEY"
```

**3. Disable the incomplete fine-tuning tab.** `main()` calls `show_fine_tuning()`, but that function is not defined. Remove `fine_tuning_tab` from the tuple assignment, remove `"Fine Tuning"` from the tab-label list, and remove its `with fine_tuning_tab:` block. Keep the remaining seven tabs. The Gold Dataset tab still provides JSONL export.

Launch from the project directory:

```bash
python -m streamlit run app.py
```

Open the local URL printed by Streamlit. API calls incur provider charges and require access to the configured model. Annotation and review can be explored locally after the startup fixes without making API calls.

## Suggested walkthrough

1. Upload and save the included sample CSV.
2. Annotate a message with its intent, sentiment, escalation decision and expected answer.
3. Approve it in Reviewer Workspace; try rejecting another item with comments.
4. Confirm only approved annotations appear in Gold Dataset.
5. Generate a response in Model Inference after configuring credentials.
6. Evaluate the response in Human Evaluation and DeepEval Evaluation.
7. Compare the human decision, automated reasons and recorded latency/token usage.
8. Approve at least ten valid examples to unlock the application's training export.

The ten-example minimum is this application's validation rule, not a statement that every training provider or model accepts the exported file. Export does not start training or establish a model-quality improvement.

## Storage and source layout

| Path | Purpose |
|---|---|
| `app.py` | Streamlit workspaces, inference, JSONL generation and validation |
| `database/database.py` | SQLite schema, queries and persistence |
| `evaluation/deepeval_service.py` | Four GEval metrics and aggregate result |
| `data/customer_support_dataset.csv` | Example input |
| `requirements.txt` | Python dependencies |

SQLite is created at `database/dataops.db`. Its six tables are `samples`, `annotations`, `reviews`, `inference_results`, `human_evaluations` and `automated_evaluations`. Back up the database before making destructive changes; keep customer data and generated databases out of public commits.

## Current limitations

- No user authentication, reviewer identities, access controls or enforced separation of annotator and reviewer roles.
- No dataset/rubric versioning, vendor workflow, consensus measurement or automated label-error detection.
- Approved expected answers can be edited without reapproval; existing evaluations are not invalidated and reference snapshots are not versioned. Avoid changing references during an evaluation run.
- Fixed customer-support taxonomy and sequential workspaces; no distributed reviewer assignment.
- Inference and evaluation send content to external model services. Local SQLite storage does not make the complete workflow offline or private.
- No implemented FLAN-T5 training routine in this checkout, despite training libraries appearing in requirements.
- No held-out train/test split, comparative model benchmark or demonstrated production adoption.
- No automated test suite is included in this project directory. The README is based on source inspection, not a successful live API walkthrough.

## Author

**Bindu Singh, PMP, CSM**

[LinkedIn](https://www.linkedin.com/in/bindu-singh-pmp-csm/) · [Project portfolio](https://github.com/bs5299-PM/AI-Project-Manager)
