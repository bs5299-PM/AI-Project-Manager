# OpsSignal
AI-assisted project operations and risk intelligence


OpsSignal connects Linear, Slack, and Notion so project information does not remain scattered across task updates, conversations, and leadership reports.



Linear remains the execution source of truth. OpsSignal synchronizes project data into SQLite, records change history, detects operational risks using deterministic rules, updates a Notion leadership dashboard, and sends targeted Slack alerts.



\## What it does



\- Synchronizes Linear projects, issues, owners, priorities, dates, health, milestones, and dependencies

\- Maintains current project state and historical changes in SQLite

\- Detects blocked, overdue, unassigned, stale, and dependency-risk conditions

\- Uses Claude to interpret possible project changes discussed in Slack

\- Requires approval from a designated project lead before updating Linear

\- Updates a Notion portfolio dashboard with current risks and next actions

\- Sends one targeted Slack alert when a risk is new or materially changes

\- Produces a weekly leadership digest from verified project information



\## System flow



1\. Linear sends project and issue changes through webhooks.

2\. OpsSignal synchronizes the updated Linear state into SQLite.

3\. The system records field-level changes in project history.

4\. Deterministic rules evaluate the stored data for operational risks.

5\. The Notion dashboard refreshes with the current project state and open risks.

6\. New or materially changed risks generate targeted Slack alerts.

7\. Claude interprets possible changes discussed in approved Slack channels.

8\. A project lead confirms or rejects each proposed change.

9\. Confirmed changes are written to Linear and synchronized through the same workflow.



\## Risk rules



OpsSignal detects:



\- High or Urgent issues that become blocked

\- High or Urgent issues that become overdue

\- High or Urgent issues without an assignee

\- Blocking issues scheduled after the work that depends on them

\- Projects whose Linear health changes to Off track

\- Active projects without a recent project update

\- Confirmed changes that create cross-team or milestone risk



Linear remains the source of truth for project health. OpsSignal reports detected risks separately and never changes health automatically.



\## Alert lifecycle



Each risk receives a stable identifier. The first detection produces one alert. Repeated checks do not resend the same alert.



Another alert is generated only when:



\- The risk’s severity or impact changes

\- It reaches an escalation threshold

\- The condition is resolved



When one change triggers multiple rules, OpsSignal consolidates them into the most specific alert to prevent notification noise.



\## Human approval and AI boundary



Claude may:



\- Interpret relevant Slack messages

\- Identify the likely Linear issue and field

\- Draft a specific old-value-to-new-value proposal

\- Summarize verified changes and risks

\- Suggest a next action based on existing project facts



Claude may not:



\- Approve a change

\- Set official priority or project health

\- Assign accountability

\- Modify Linear without confirmation

\- Invent missing project information

\- Close or validate a risk



\## Technology



\- Python

\- Flask

\- SQLite

\- Linear GraphQL API and webhooks

\- Slack Web API and interactive actions

\- Notion API

\- Anthropic Claude API



\## Repository structure



```text

OpsSignal/

├── README.md

├── PRODUCT\_SPEC.md

├── requirements.txt

├── .env.example

├── .gitignore

├── alerting.py

├── approvals.py

├── coordination.py

├── db.py

├── digest.py

├── linear\_client.py

├── notion\_sync.py

├── proposals.py

├── risk\_engine.py

├── slack\_client.py

├── slack\_routes.py

├── sync.py

├── webhook\_handlers.py

├── webhook\_server.py

└── tests/

```



\## Running locally



Install the dependencies:



```bash

pip install -r requirements.txt

```



Copy `.env.example` to `.env` and add credentials for the demo Linear, Slack, Notion, and Anthropic accounts.



Run an initial synchronization:



```bash

python sync.py

```



Start the webhook server:



```bash

python webhook\_server.py

```



The server runs locally on port `8000`.



\## Data and security



\- Secrets are loaded from `.env` and are never committed.

\- Webhook signatures are verified before events are processed.

\- Database and log files remain local.

\- Official Linear changes require human confirmation.

\- The public demonstration uses synthetic project information only.



\## Why I built it



Deployment-heavy teams often have execution data in a project tracker, decisions in Slack, and leadership reporting in a separate workspace. OpsSignal demonstrates how those systems can be connected without replacing them or allowing AI to make unapproved operational decisions.

