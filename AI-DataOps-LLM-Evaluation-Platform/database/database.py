import sqlite3
from pathlib import Path

import pandas as pd


DATABASE_FOLDER = Path(__file__).resolve().parent
DATABASE_PATH = DATABASE_FOLDER / "dataops.db"


def get_connection() -> sqlite3.Connection:
    """Create and return a SQLite database connection."""

    DATABASE_FOLDER.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DATABASE_PATH)
    connection.execute("PRAGMA foreign_keys = ON;")

    return connection


def initialize_database() -> None:
    """Create all application tables if they do not already exist."""

    with get_connection() as connection:

        # Milestone 1: raw customer-support samples
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS samples (
                database_id INTEGER PRIMARY KEY AUTOINCREMENT,
                sample_id TEXT NOT NULL UNIQUE,
                customer_message TEXT NOT NULL,
                annotation_status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # Milestone 2: human annotations
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS annotations (
                annotation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                sample_database_id INTEGER NOT NULL UNIQUE,
                intent TEXT NOT NULL,
                sentiment TEXT NOT NULL,
                escalation_required INTEGER NOT NULL DEFAULT 0,
                expected_answer TEXT NOT NULL,
                annotated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (sample_database_id)
                    REFERENCES samples(database_id)
                    ON DELETE CASCADE
            );
            """
        )

        # Milestone 3: reviewer decisions
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reviews (
                review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                annotation_id INTEGER NOT NULL UNIQUE,
                review_status TEXT NOT NULL,
                reviewer_comments TEXT,
                reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (annotation_id)
                    REFERENCES annotations(annotation_id)
                    ON DELETE CASCADE
            );
            """
        )

        # Milestone 4: model inference results
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS inference_results (
                inference_id INTEGER PRIMARY KEY AUTOINCREMENT,
                annotation_id INTEGER NOT NULL,
                model_name TEXT NOT NULL,
                generated_answer TEXT,
                latency_seconds REAL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER,
                inference_status TEXT NOT NULL,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (annotation_id)
                    REFERENCES annotations(annotation_id)
                    ON DELETE CASCADE
            );
            """
        )

        # Milestone 5A: manual evaluation of AI answers
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS human_evaluations (
                evaluation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                inference_id INTEGER NOT NULL UNIQUE,
                correctness_score INTEGER NOT NULL,
                relevance_score INTEGER NOT NULL,
                completeness_score INTEGER NOT NULL,
                helpfulness_score INTEGER NOT NULL,
                hallucination_detected INTEGER NOT NULL DEFAULT 0,
                evaluation_status TEXT NOT NULL,
                evaluator_comments TEXT,
                evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (inference_id)
                    REFERENCES inference_results(inference_id)
                    ON DELETE CASCADE
            );
            """
        )

        # Milestone 5B: DeepEval automated evaluations
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS automated_evaluations (
                automated_evaluation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                inference_id INTEGER NOT NULL UNIQUE,
                correctness_score REAL NOT NULL,
                relevance_score REAL NOT NULL,
                completeness_score REAL NOT NULL,
                helpfulness_score REAL NOT NULL,
                overall_score REAL NOT NULL,
                evaluation_status TEXT NOT NULL,
                correctness_reason TEXT,
                relevance_reason TEXT,
                completeness_reason TEXT,
                helpfulness_reason TEXT,
                evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (inference_id)
                    REFERENCES inference_results(inference_id)
                    ON DELETE CASCADE
            );
            """
        )


# ---------------------------------------------------------
# Dataset ingestion
# ---------------------------------------------------------

def save_samples(dataframe: pd.DataFrame) -> tuple[int, int]:
    """Save validated samples and return inserted and skipped counts."""

    inserted_count = 0
    skipped_count = 0

    with get_connection() as connection:
        cursor = connection.cursor()

        for row in dataframe.itertuples(index=False):
            try:
                cursor.execute(
                    """
                    INSERT INTO samples (
                        sample_id,
                        customer_message
                    )
                    VALUES (?, ?);
                    """,
                    (
                        str(row.sample_id).strip(),
                        str(row.customer_message).strip(),
                    ),
                )
                inserted_count += 1

            except sqlite3.IntegrityError:
                skipped_count += 1

    return inserted_count, skipped_count


def get_all_samples() -> pd.DataFrame:
    """Return all saved samples."""

    with get_connection() as connection:
        return pd.read_sql_query(
            """
            SELECT
                database_id,
                sample_id,
                customer_message,
                annotation_status,
                created_at
            FROM samples
            ORDER BY database_id;
            """,
            connection,
        )


# ---------------------------------------------------------
# Annotation workspace
# ---------------------------------------------------------

def get_next_pending_sample() -> dict | None:
    """Return the next sample waiting for annotation."""

    with get_connection() as connection:
        connection.row_factory = sqlite3.Row

        row = connection.execute(
            """
            SELECT
                database_id,
                sample_id,
                customer_message
            FROM samples
            WHERE annotation_status = 'pending'
            ORDER BY database_id
            LIMIT 1;
            """
        ).fetchone()

    return dict(row) if row else None


def save_annotation(
    sample_database_id: int,
    intent: str,
    sentiment: str,
    escalation_required: bool,
    expected_answer: str,
) -> None:
    """Save one annotation and mark its sample as annotated."""

    cleaned_expected_answer = expected_answer.strip()

    if not cleaned_expected_answer:
        raise ValueError("Expected answer cannot be empty.")

    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO annotations (
                sample_database_id,
                intent,
                sentiment,
                escalation_required,
                expected_answer
            )
            VALUES (?, ?, ?, ?, ?);
            """,
            (
                sample_database_id,
                intent,
                sentiment,
                int(escalation_required),
                cleaned_expected_answer,
            ),
        )

        cursor.execute(
            """
            UPDATE samples
            SET annotation_status = 'annotated'
            WHERE database_id = ?;
            """,
            (sample_database_id,),
        )


def get_all_annotations() -> pd.DataFrame:
    """Return annotations with their source samples."""

    with get_connection() as connection:
        return pd.read_sql_query(
            """
            SELECT
                a.annotation_id,
                a.sample_database_id,
                s.sample_id,
                s.customer_message,
                a.intent,
                a.sentiment,
                a.escalation_required,
                a.expected_answer,
                a.annotated_at
            FROM annotations AS a
            JOIN samples AS s
                ON a.sample_database_id = s.database_id
            ORDER BY a.annotation_id;
            """,
            connection,
        )


def get_annotation_progress() -> tuple[int, int]:
    """Return annotated sample count and total sample count."""

    with get_connection() as connection:
        total_count = connection.execute(
            "SELECT COUNT(*) FROM samples;"
        ).fetchone()[0]

        annotated_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM samples
            WHERE annotation_status = 'annotated';
            """
        ).fetchone()[0]

    return annotated_count, total_count


# ---------------------------------------------------------
# Reviewer workspace
# ---------------------------------------------------------

def get_next_pending_review() -> dict | None:
    """Return the next annotation waiting for review."""

    with get_connection() as connection:
        connection.row_factory = sqlite3.Row

        row = connection.execute(
            """
            SELECT
                a.annotation_id,
                s.sample_id,
                s.customer_message,
                a.intent,
                a.sentiment,
                a.escalation_required,
                a.expected_answer,
                a.annotated_at
            FROM annotations AS a
            JOIN samples AS s
                ON a.sample_database_id = s.database_id
            LEFT JOIN reviews AS r
                ON a.annotation_id = r.annotation_id
            WHERE r.review_id IS NULL
            ORDER BY a.annotation_id
            LIMIT 1;
            """
        ).fetchone()

    return dict(row) if row else None


def save_review(
    annotation_id: int,
    review_status: str,
    reviewer_comments: str,
) -> None:
    """Save an annotation approval or rejection."""

    if review_status not in {"Approved", "Rejected"}:
        raise ValueError(
            "Review status must be Approved or Rejected."
        )

    cleaned_comments = reviewer_comments.strip()

    if review_status == "Rejected" and not cleaned_comments:
        raise ValueError(
            "Reviewer comments are required when rejecting."
        )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO reviews (
                annotation_id,
                review_status,
                reviewer_comments
            )
            VALUES (?, ?, ?);
            """,
            (
                annotation_id,
                review_status,
                cleaned_comments or None,
            ),
        )


def get_all_reviews() -> pd.DataFrame:
    """Return all completed reviews."""

    with get_connection() as connection:
        return pd.read_sql_query(
            """
            SELECT
                r.review_id,
                r.review_status,
                r.reviewer_comments,
                r.reviewed_at,
                a.annotation_id,
                s.sample_id,
                s.customer_message,
                a.intent,
                a.sentiment,
                a.escalation_required,
                a.expected_answer
            FROM reviews AS r
            JOIN annotations AS a
                ON r.annotation_id = a.annotation_id
            JOIN samples AS s
                ON a.sample_database_id = s.database_id
            ORDER BY r.review_id;
            """,
            connection,
        )


def get_review_progress() -> tuple[int, int]:
    """Return reviewed annotation count and total annotation count."""

    with get_connection() as connection:
        total_annotations = connection.execute(
            "SELECT COUNT(*) FROM annotations;"
        ).fetchone()[0]

        reviewed_annotations = connection.execute(
            "SELECT COUNT(*) FROM reviews;"
        ).fetchone()[0]

    return reviewed_annotations, total_annotations


def get_gold_dataset() -> pd.DataFrame:
    """Return reviewer-approved annotations."""

    with get_connection() as connection:
        return pd.read_sql_query(
            """
            SELECT
                a.annotation_id,
                s.database_id AS sample_database_id,
                s.sample_id,
                s.customer_message,
                a.intent,
                a.sentiment,
                a.escalation_required,
                a.expected_answer,
                r.reviewed_at
            FROM reviews AS r
            JOIN annotations AS a
                ON r.annotation_id = a.annotation_id
            JOIN samples AS s
                ON a.sample_database_id = s.database_id
            WHERE r.review_status = 'Approved'
            ORDER BY s.database_id;
            """,
            connection,
        )


# ---------------------------------------------------------
# Model inference
# ---------------------------------------------------------

def get_approved_samples_for_inference() -> pd.DataFrame:
    """Return approved annotations without a successful inference."""

    with get_connection() as connection:
        return pd.read_sql_query(
            """
            SELECT
                a.annotation_id,
                s.sample_id,
                s.customer_message,
                a.expected_answer
            FROM annotations AS a
            JOIN samples AS s
                ON a.sample_database_id = s.database_id
            JOIN reviews AS r
                ON a.annotation_id = r.annotation_id
            WHERE r.review_status = 'Approved'
              AND NOT EXISTS (
                  SELECT 1
                  FROM inference_results AS i
                  WHERE i.annotation_id = a.annotation_id
                    AND i.inference_status = 'success'
              )
            ORDER BY a.annotation_id;
            """,
            connection,
        )


def save_inference_result(
    annotation_id: int,
    model_name: str,
    generated_answer: str | None,
    latency_seconds: float | None,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
    inference_status: str,
    error_message: str | None = None,
) -> None:
    """Save one successful or failed model inference."""

    if inference_status not in {"success", "failed"}:
        raise ValueError(
            "Inference status must be success or failed."
        )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO inference_results (
                annotation_id,
                model_name,
                generated_answer,
                latency_seconds,
                input_tokens,
                output_tokens,
                total_tokens,
                inference_status,
                error_message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                annotation_id,
                model_name,
                generated_answer,
                latency_seconds,
                input_tokens,
                output_tokens,
                total_tokens,
                inference_status,
                error_message,
            ),
        )


def get_all_inference_results() -> pd.DataFrame:
    """Return all model inference attempts."""

    with get_connection() as connection:
        return pd.read_sql_query(
            """
            SELECT
                i.inference_id,
                i.annotation_id,
                s.sample_id,
                s.customer_message,
                a.expected_answer,
                i.model_name,
                i.generated_answer,
                i.latency_seconds,
                i.input_tokens,
                i.output_tokens,
                i.total_tokens,
                i.inference_status,
                i.error_message,
                i.created_at
            FROM inference_results AS i
            JOIN annotations AS a
                ON i.annotation_id = a.annotation_id
            JOIN samples AS s
                ON a.sample_database_id = s.database_id
            ORDER BY i.inference_id;
            """,
            connection,
        )


def get_inference_progress() -> tuple[int, int]:
    """Return successful inference count and approved sample count."""

    with get_connection() as connection:
        total_approved = connection.execute(
            """
            SELECT COUNT(*)
            FROM reviews
            WHERE review_status = 'Approved';
            """
        ).fetchone()[0]

        successful_inferences = connection.execute(
            """
            SELECT COUNT(DISTINCT annotation_id)
            FROM inference_results
            WHERE inference_status = 'success';
            """
        ).fetchone()[0]

    return successful_inferences, total_approved


# ---------------------------------------------------------
# Human evaluation
# ---------------------------------------------------------

def get_next_pending_human_evaluation() -> dict | None:
    """Return the next successful inference awaiting human evaluation."""

    with get_connection() as connection:
        connection.row_factory = sqlite3.Row

        row = connection.execute(
            """
            SELECT
                i.inference_id,
                i.annotation_id,
                s.sample_id,
                s.customer_message,
                a.expected_answer,
                i.generated_answer,
                i.model_name,
                i.latency_seconds,
                i.input_tokens,
                i.output_tokens,
                i.total_tokens
            FROM inference_results AS i
            JOIN annotations AS a
                ON i.annotation_id = a.annotation_id
            JOIN samples AS s
                ON a.sample_database_id = s.database_id
            LEFT JOIN human_evaluations AS h
                ON i.inference_id = h.inference_id
            WHERE i.inference_status = 'success'
              AND h.evaluation_id IS NULL
            ORDER BY i.inference_id
            LIMIT 1;
            """
        ).fetchone()

    return dict(row) if row else None


def save_human_evaluation(
    inference_id: int,
    correctness_score: int,
    relevance_score: int,
    completeness_score: int,
    helpfulness_score: int,
    hallucination_detected: bool,
    evaluation_status: str,
    evaluator_comments: str,
) -> None:
    """Save one manual evaluation of an AI-generated answer."""

    scores = [
        correctness_score,
        relevance_score,
        completeness_score,
        helpfulness_score,
    ]

    if any(score < 1 or score > 5 for score in scores):
        raise ValueError("Every score must be between 1 and 5.")

    if evaluation_status not in {"Pass", "Fail"}:
        raise ValueError(
            "Evaluation status must be Pass or Fail."
        )

    cleaned_comments = evaluator_comments.strip()

    if evaluation_status == "Fail" and not cleaned_comments:
        raise ValueError(
            "Comments are required when an answer fails."
        )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO human_evaluations (
                inference_id,
                correctness_score,
                relevance_score,
                completeness_score,
                helpfulness_score,
                hallucination_detected,
                evaluation_status,
                evaluator_comments
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                inference_id,
                correctness_score,
                relevance_score,
                completeness_score,
                helpfulness_score,
                int(hallucination_detected),
                evaluation_status,
                cleaned_comments or None,
            ),
        )


def get_all_human_evaluations() -> pd.DataFrame:
    """Return all completed human evaluations."""

    with get_connection() as connection:
        return pd.read_sql_query(
            """
            SELECT
                h.evaluation_id,
                h.inference_id,
                s.sample_id,
                s.customer_message,
                a.expected_answer,
                i.generated_answer,
                i.model_name,
                i.latency_seconds,
                i.total_tokens,
                h.correctness_score,
                h.relevance_score,
                h.completeness_score,
                h.helpfulness_score,
                h.hallucination_detected,
                h.evaluation_status,
                h.evaluator_comments,
                h.evaluated_at
            FROM human_evaluations AS h
            JOIN inference_results AS i
                ON h.inference_id = i.inference_id
            JOIN annotations AS a
                ON i.annotation_id = a.annotation_id
            JOIN samples AS s
                ON a.sample_database_id = s.database_id
            ORDER BY h.evaluation_id;
            """,
            connection,
        )


def get_human_evaluation_progress() -> tuple[int, int]:
    """Return completed human evaluations and successful inferences."""

    with get_connection() as connection:
        total_inferences = connection.execute(
            """
            SELECT COUNT(*)
            FROM inference_results
            WHERE inference_status = 'success';
            """
        ).fetchone()[0]

        completed_evaluations = connection.execute(
            "SELECT COUNT(*) FROM human_evaluations;"
        ).fetchone()[0]

    return completed_evaluations, total_inferences


# ---------------------------------------------------------
# DeepEval automated evaluation
# ---------------------------------------------------------

def get_next_pending_automated_evaluation() -> dict | None:
    """Return the next successful inference awaiting DeepEval."""

    with get_connection() as connection:
        connection.row_factory = sqlite3.Row

        row = connection.execute(
            """
            SELECT
                i.inference_id,
                i.annotation_id,
                s.sample_id,
                s.customer_message,
                a.expected_answer,
                i.generated_answer,
                i.model_name,
                i.latency_seconds,
                i.input_tokens,
                i.output_tokens,
                i.total_tokens
            FROM inference_results AS i
            JOIN annotations AS a
                ON i.annotation_id = a.annotation_id
            JOIN samples AS s
                ON a.sample_database_id = s.database_id
            LEFT JOIN automated_evaluations AS ae
                ON i.inference_id = ae.inference_id
            WHERE i.inference_status = 'success'
              AND ae.automated_evaluation_id IS NULL
            ORDER BY i.inference_id
            LIMIT 1;
            """
        ).fetchone()

    return dict(row) if row else None


def save_automated_evaluation(
    inference_id: int,
    correctness_score: float,
    relevance_score: float,
    completeness_score: float,
    helpfulness_score: float,
    overall_score: float,
    evaluation_status: str,
    correctness_reason: str,
    relevance_reason: str,
    completeness_reason: str,
    helpfulness_reason: str,
) -> None:
    """Save one DeepEval result."""

    if evaluation_status not in {"Pass", "Fail"}:
        raise ValueError(
            "Evaluation status must be Pass or Fail."
        )

    scores = [
        correctness_score,
        relevance_score,
        completeness_score,
        helpfulness_score,
        overall_score,
    ]

    if any(score < 0 or score > 1 for score in scores):
        raise ValueError(
            "DeepEval scores must be between 0 and 1."
        )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO automated_evaluations (
                inference_id,
                correctness_score,
                relevance_score,
                completeness_score,
                helpfulness_score,
                overall_score,
                evaluation_status,
                correctness_reason,
                relevance_reason,
                completeness_reason,
                helpfulness_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                inference_id,
                correctness_score,
                relevance_score,
                completeness_score,
                helpfulness_score,
                overall_score,
                evaluation_status,
                correctness_reason,
                relevance_reason,
                completeness_reason,
                helpfulness_reason,
            ),
        )


def get_automated_evaluation_progress() -> tuple[int, int]:
    """Return completed DeepEval count and successful inference count."""

    with get_connection() as connection:
        total_inferences = connection.execute(
            """
            SELECT COUNT(*)
            FROM inference_results
            WHERE inference_status = 'success';
            """
        ).fetchone()[0]

        completed_evaluations = connection.execute(
            "SELECT COUNT(*) FROM automated_evaluations;"
        ).fetchone()[0]

    return completed_evaluations, total_inferences


def get_all_automated_evaluations() -> pd.DataFrame:
    """Return all saved DeepEval results."""

    with get_connection() as connection:
        return pd.read_sql_query(
            """
            SELECT
                ae.automated_evaluation_id,
                ae.inference_id,
                s.sample_id,
                s.customer_message,
                a.expected_answer,
                i.generated_answer,
                i.model_name,
                ae.correctness_score,
                ae.relevance_score,
                ae.completeness_score,
                ae.helpfulness_score,
                ae.overall_score,
                ae.evaluation_status,
                ae.correctness_reason,
                ae.relevance_reason,
                ae.completeness_reason,
                ae.helpfulness_reason,
                i.latency_seconds,
                i.total_tokens,
                ae.evaluated_at
            FROM automated_evaluations AS ae
            JOIN inference_results AS i
                ON ae.inference_id = i.inference_id
            JOIN annotations AS a
                ON i.annotation_id = a.annotation_id
            JOIN samples AS s
                ON a.sample_database_id = s.database_id
            ORDER BY ae.automated_evaluation_id;
            """,
            connection,
        )
def update_expected_answer(
    annotation_id: int,
    expected_answer: str,
) -> None:

    with get_connection() as connection:

        connection.execute(
            """
            UPDATE annotations
            SET expected_answer = ?
            WHERE annotation_id = ?;
            """,
            (
                expected_answer.strip(),
                annotation_id,
            ),
        )

if __name__ == "__main__":
    initialize_database()

    print("Database initialized successfully.")
    print(f"Database location: {DATABASE_PATH}")