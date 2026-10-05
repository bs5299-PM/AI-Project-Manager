"""Fetches projects and issues from Linear. The API key is read from .env and never printed."""
import os

import requests
from dotenv import load_dotenv

LINEAR_URL = "https://api.linear.app/graphql"

PROJECTS_QUERY = """
query($after: String) {
  projects(first: 50, after: $after) {
    nodes {
      id
      name
      url
      status { name }
      lead { name }
      targetDate
      projectMilestones { nodes { name targetDate status } }
      projectUpdates(first: 10) { nodes { health createdAt } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

ISSUES_QUERY = """
query($after: String) {
  issues(first: 50, after: $after) {
    nodes {
      id
      identifier
      title
      priorityLabel
      dueDate
      project { id }
      assignee { name }
      team { name }
      state { name }
      inverseRelations(first: 20) { nodes { type issue { id } } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def _api_key():
    load_dotenv()
    key = os.getenv("LINEAR_API_KEY", "").strip()
    if not key:
        raise RuntimeError("LINEAR_API_KEY is missing or empty in .env")
    return key


def _run(query, variables, key):
    resp = requests.post(
        LINEAR_URL,
        json={"query": query, "variables": variables},
        headers={"Authorization": key, "Content-Type": "application/json"},
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Linear returned HTTP {resp.status_code}: {resp.text[:300]}")
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"Linear returned errors: {body['errors']}")
    return body["data"]


def _fetch_all(query, field, key):
    nodes, after = [], None
    while True:
        data = _run(query, {"after": after}, key)[field]
        nodes.extend(data["nodes"])
        if not data["pageInfo"]["hasNextPage"]:
            return nodes
        after = data["pageInfo"]["endCursor"]


PRIORITY_NUMBERS = {"No priority": 0, "Urgent": 1, "High": 2, "Medium": 3, "Low": 4}

ISSUE_FIELDS_QUERY = "query($id: String!) { issue(id: $id) { dueDate priorityLabel } }"

ISSUE_UPDATE_MUTATION = """
mutation($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) { success issue { dueDate priorityLabel } }
}
"""


def get_issue_fields(linear_id):
    """Live values of the Slack-editable fields, straight from Linear."""
    issue = _run(ISSUE_FIELDS_QUERY, {"id": linear_id}, _api_key())["issue"]
    return {"due_date": issue["dueDate"], "priority": issue["priorityLabel"]}


def update_issue(linear_id, field, value):
    """Write one change to a Linear issue. Only due_date and priority are supported."""
    if field == "due_date":
        change = {"dueDate": value}
    elif field == "priority":
        change = {"priority": PRIORITY_NUMBERS[value]}
    else:
        raise ValueError(f"Field not supported for Linear updates: {field}")
    result = _run(ISSUE_UPDATE_MUTATION, {"id": linear_id, "input": change}, _api_key())["issueUpdate"]
    if not result["success"]:
        raise RuntimeError("Linear did not accept the update")


def fetch_projects():
    return _fetch_all(PROJECTS_QUERY, "projects", _api_key())


def fetch_issues():
    return _fetch_all(ISSUES_QUERY, "issues", _api_key())
