import time
import json

import pandas as pd
import streamlit as st
from openai import OpenAI
from deepeval_service import run_deepeval


from database.database import (
    get_all_annotations,
    get_all_human_evaluations,
    get_all_inference_results,
    get_all_reviews,
    get_all_samples,
    get_annotation_progress,
    get_approved_samples_for_inference,
    get_gold_dataset,
    get_human_evaluation_progress,
    get_inference_progress,
    get_next_pending_human_evaluation,
    get_next_pending_review,
    get_next_pending_sample,
    get_review_progress,
    initialize_database,
    save_annotation,
    save_human_evaluation,
    save_inference_result,
    save_review,
    save_samples,
    get_all_automated_evaluations,
    get_automated_evaluation_progress,
    get_next_pending_automated_evaluation,
    save_automated_evaluation,
    update_expected_answer
)


# Keep your key local. Remove it before uploading to GitHub.
OPENAI_API_KEY = ""
MODEL_NAME = "gpt-5"

client = OpenAI(api_key=OPENAI_API_KEY)

REQUIRED_COLUMNS = {"sample_id", "customer_message"}


# ---------------------------------------------------------
# Dataset validation
# ---------------------------------------------------------

def validate_dataset(dataframe: pd.DataFrame) -> list[str]:
    """Validate the uploaded CSV."""

    errors: list[str] = []

    missing_columns = REQUIRED_COLUMNS - set(dataframe.columns)

    if missing_columns:
        errors.append(
            "Missing required column(s): "
            + ", ".join(sorted(missing_columns))
        )
        return errors

    empty_sample_ids = dataframe["sample_id"].isna()

    if empty_sample_ids.any():
        row_numbers = (
            dataframe.index[empty_sample_ids] + 2
        ).tolist()

        errors.append(
            "Empty sample_id found in CSV row(s): "
            + ", ".join(map(str, row_numbers))
        )

    customer_messages = dataframe[
        "customer_message"
    ].astype("string")

    empty_messages = (
        customer_messages.isna()
        | customer_messages.str.strip().eq("")
    )

    if empty_messages.any():
        row_numbers = (
            dataframe.index[empty_messages] + 2
        ).tolist()

        errors.append(
            "Empty customer_message found in CSV row(s): "
            + ", ".join(map(str, row_numbers))
        )

    duplicate_ids = (
        dataframe.loc[
            dataframe["sample_id"].duplicated(keep=False),
            "sample_id",
        ]
        .dropna()
        .unique()
    )

    if len(duplicate_ids) > 0:
        errors.append(
            "Duplicate sample_id value(s) found: "
            + ", ".join(map(str, duplicate_ids))
        )

    return errors


# ---------------------------------------------------------
# Dataset ingestion
# ---------------------------------------------------------

def show_dataset_ingestion() -> None:
    st.header("Dataset Ingestion")

    dataset_name = st.text_input(
        "Dataset name",
        placeholder="Example: Customer Support Messages",
    )

    dataset_description = st.text_area(
        "Dataset description",
        placeholder="Describe the purpose of this dataset.",
        height=100,
    )

    uploaded_file = st.file_uploader(
        "Upload a CSV file",
        type=["csv"],
        help=(
            "The CSV must contain sample_id and "
            "customer_message columns."
        ),
    )

    if uploaded_file is not None:
        try:
            dataframe = pd.read_csv(uploaded_file)

        except pd.errors.EmptyDataError:
            st.error("The uploaded CSV is empty.")
            return

        except pd.errors.ParserError:
            st.error("The CSV could not be read.")
            return

        except Exception as error:
            st.error(f"Unexpected error: {error}")
            return

        validation_errors = validate_dataset(dataframe)

        if validation_errors:
            st.error(
                f"Validation failed with "
                f"{len(validation_errors)} issue(s)."
            )

            for error in validation_errors:
                st.write(f"❌ {error}")

            return

        st.success("Validation passed.")

        col1, col2 = st.columns(2)
        col1.metric("Rows", len(dataframe))
        col2.metric("Columns", len(dataframe.columns))

        st.dataframe(
            dataframe.head(10),
            use_container_width=True,
            hide_index=True,
        )

        st.write(
            {
                "dataset_name": dataset_name,
                "dataset_description": dataset_description,
                "uploaded_filename": uploaded_file.name,
            }
        )

        if st.button(
            "Save validated samples",
            type="primary",
        ):
            inserted_count, skipped_count = save_samples(
                dataframe
            )

            if inserted_count:
                st.success(
                    f"{inserted_count} sample(s) saved."
                )

            if skipped_count:
                st.warning(
                    f"{skipped_count} duplicate sample(s) skipped."
                )

    st.divider()
    st.subheader("Saved Samples")

    samples = get_all_samples()

    if samples.empty:
        st.info("No samples saved yet.")
    else:
        st.dataframe(
            samples,
            use_container_width=True,
            hide_index=True,
        )


# ---------------------------------------------------------
# Annotation workspace
# ---------------------------------------------------------

def show_annotation_workspace() -> None:
    st.header("Annotation Workspace")

    annotated_count, total_count = get_annotation_progress()

    if total_count == 0:
        st.info("Upload and save a dataset first.")
        return

    st.write(
        f"Progress: {annotated_count} of "
        f"{total_count} samples annotated"
    )

    st.progress(annotated_count / total_count)

    sample = get_next_pending_sample()

    if sample is None:
        st.success("All samples have been annotated.")

    else:
        st.subheader(f"Sample ID: {sample['sample_id']}")
        st.info(sample["customer_message"])

        with st.form("annotation_form"):
            intent = st.selectbox(
                "Intent",
                [
                    "Password Reset",
                    "Account Access",
                    "Billing",
                    "Payment Issue",
                    "Order Tracking",
                    "Refund",
                    "Cancellation",
                    "Subscription Management",
                    "Technical Support",
                    "Security",
                    "Product Return",
                    "Human Support Request",
                    "Other",
                ],
            )

            sentiment = st.selectbox(
                "Sentiment",
                ["Positive", "Neutral", "Negative"],
            )

            escalation_required = st.checkbox(
                "Escalation required"
            )

            expected_answer = st.text_area(
                "Expected answer",
                placeholder="Enter the ideal support response.",
                height=150,
            )

            submitted = st.form_submit_button(
                "Submit annotation",
                type="primary",
            )

        if submitted:
            if not expected_answer.strip():
                st.error("Expected answer cannot be empty.")

            else:
                try:
                    save_annotation(
                        sample_database_id=sample["database_id"],
                        intent=intent,
                        sentiment=sentiment,
                        escalation_required=escalation_required,
                        expected_answer=expected_answer,
                    )

                except Exception as error:
                    st.error(
                        f"Could not save annotation: {error}"
                    )

                else:
                    st.success("Annotation saved.")
                    st.rerun()

    st.divider()
    st.subheader("Completed Annotations")

    annotations = get_all_annotations()

    if annotations.empty:
        st.info("No annotations submitted yet.")

    else:
        display_data = annotations.copy()

        display_data["escalation_required"] = (
            display_data["escalation_required"]
            .map({1: "Yes", 0: "No"})
        )

        st.dataframe(
            display_data,
            use_container_width=True,
            hide_index=True,
        )


# ---------------------------------------------------------
# Reviewer workspace
# ---------------------------------------------------------

def show_reviewer_workspace() -> None:
    st.header("Reviewer Workspace")

    reviewed_count, total_count = get_review_progress()

    if total_count == 0:
        st.info("Complete at least one annotation first.")
        return

    st.write(
        f"Progress: {reviewed_count} of "
        f"{total_count} annotations reviewed"
    )

    st.progress(reviewed_count / total_count)

    review = get_next_pending_review()

    if review is None:
        st.success("All annotations have been reviewed.")

    else:
        st.subheader(f"Sample ID: {review['sample_id']}")

        st.markdown("#### Customer Message")
        st.info(review["customer_message"])

        st.markdown("#### Submitted Annotation")

        col1, col2, col3 = st.columns(3)

        col1.write("**Intent**")
        col1.write(review["intent"])

        col2.write("**Sentiment**")
        col2.write(review["sentiment"])

        col3.write("**Escalation**")
        col3.write(
            "Yes" if review["escalation_required"] else "No"
        )

        st.write("**Expected Answer**")
        st.write(review["expected_answer"])

        with st.form("review_form"):
            review_status = st.radio(
                "Review decision",
                ["Approved", "Rejected"],
                horizontal=True,
            )

            reviewer_comments = st.text_area(
                "Reviewer comments",
                placeholder=(
                    "Required when rejecting; optional "
                    "when approving."
                ),
                height=120,
            )

            submitted = st.form_submit_button(
                "Submit review",
                type="primary",
            )

        if submitted:
            try:
                save_review(
                    annotation_id=review["annotation_id"],
                    review_status=review_status,
                    reviewer_comments=reviewer_comments,
                )

            except Exception as error:
                st.error(f"Could not save review: {error}")

            else:
                st.success("Review saved.")
                st.rerun()

    st.divider()
    st.subheader("Completed Reviews")

    reviews = get_all_reviews()

    if reviews.empty:
        st.info("No completed reviews.")

    else:
        display_data = reviews.copy()

        display_data["escalation_required"] = (
            display_data["escalation_required"]
            .map({1: "Yes", 0: "No"})
        )

        st.dataframe(
            display_data,
            use_container_width=True,
            hide_index=True,
        )


# ---------------------------------------------------------
# Gold dataset
# ---------------------------------------------------------

def show_gold_dataset() -> None:
    st.header("Gold Dataset")

    gold_dataset = get_gold_dataset()

    if gold_dataset.empty:
        st.info("Approve annotations in the Reviewer Workspace.")
        return

    display_data = gold_dataset.copy()
    display_data["escalation_required"] = (
        display_data["escalation_required"].map({1: "Yes", 0: "No"})
    )

    col1, col2 = st.columns(2)
    col1.metric("Approved Gold Samples", len(display_data))
    col2.metric(
        "Escalation Required",
        int((display_data["escalation_required"] == "Yes").sum()),
    )

    st.dataframe(
        display_data,
        use_container_width=True,
        hide_index=True,
    )

    # Edit approved expected answers
    st.divider()
    st.subheader("Edit Gold Dataset")

    sample_options = display_data["sample_id"].tolist()
    selected_sample = st.selectbox(
        "Select Sample",
        sample_options,
        key="gold_dataset_sample_selector",
    )

    selected_row = display_data[
        display_data["sample_id"] == selected_sample
    ].iloc[0]

    st.markdown("#### Customer Message")
    st.info(selected_row["customer_message"])

    edited_answer = st.text_area(
        "Expected Answer",
        value=str(selected_row["expected_answer"]),
        height=180,
        key=f"gold_answer_{selected_sample}",
    )

    if st.button(
        "Update Gold Answer",
        type="primary",
        key="update_gold_answer_button",
    ):
        try:
            update_expected_answer(
                annotation_id=int(selected_row["annotation_id"]),
                expected_answer=edited_answer,
            )
        except Exception as error:
            st.error(f"Could not update the Gold answer: {error}")
        else:
            st.success("Gold answer updated successfully.")
            st.rerun()

    # Fine-tuning export
    st.divider()
    st.subheader("Fine-Tuning Dataset Export")

    jsonl_content = create_fine_tuning_jsonl(gold_dataset)
    validation_errors, valid_examples = validate_fine_tuning_jsonl(
        jsonl_content
    )

    export_col1, export_col2 = st.columns(2)
    export_col1.metric("Gold Samples", len(gold_dataset))
    export_col2.metric("Valid Training Examples", valid_examples)

    if validation_errors:
        st.error(f"Validation found {len(validation_errors)} issue(s).")
        for error in validation_errors:
            st.write(f"❌ {error}")
    else:
        st.success("Validation passed. training.jsonl is ready.")
        st.download_button(
            label="Download training.jsonl",
            data=jsonl_content,
            file_name="training.jsonl",
            mime="application/jsonl",
            type="primary",
        )

    if jsonl_content:
        with st.expander("Preview First 3 Training Examples"):
            for index, line in enumerate(
                jsonl_content.splitlines()[:3],
                start=1,
            ):
                st.markdown(f"**Example {index}**")
                try:
                    st.json(json.loads(line))
                except json.JSONDecodeError as error:
                    st.error(f"Example {index} contains invalid JSON: {error}")


def run_model_inference(
    annotation_id: int,
    customer_message: str,
) -> None:
    """Generate and save one AI support answer."""

    start_time = time.perf_counter()

    try:
        response = client.responses.create(
            model=MODEL_NAME,
            instructions=(
                "You are a professional customer-support assistant. "
                "Respond clearly, empathetically, and concisely. "
                "Explain appropriate next steps. Do not falsely claim "
                "that you completed an account change, refund, "
                "cancellation, or other external action."
            ),
            input=customer_message,
        )

        latency = time.perf_counter() - start_time
        usage = response.usage

        save_inference_result(
            annotation_id=annotation_id,
            model_name=response.model or MODEL_NAME,
            generated_answer=response.output_text.strip(),
            latency_seconds=round(latency, 3),
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            inference_status="success",
            error_message=None,
        )

    except Exception as error:
        latency = time.perf_counter() - start_time
        save_inference_result(
            annotation_id=annotation_id,
            model_name=MODEL_NAME,
            generated_answer=None,
            latency_seconds=round(latency, 3),
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            inference_status="failed",
            error_message=str(error),
        )
        raise


def show_model_inference() -> None:
    st.header("Model Inference")

    if not OPENAI_API_KEY or OPENAI_API_KEY == "PASTE_YOUR_OPENAI_API_KEY_HERE":
        st.error("Add your OpenAI API key in app.py.")
        return

    successful_count, approved_count = (
        get_inference_progress()
    )

    if approved_count == 0:
        st.info("Approve an annotation first.")
        return

    st.write(
        f"Progress: {successful_count} of "
        f"{approved_count} approved samples processed"
    )

    st.progress(successful_count / approved_count)

    pending_samples = get_approved_samples_for_inference()

    if pending_samples.empty:
        st.success(
            "All approved samples have successful inference results."
        )

    else:
        current_sample = pending_samples.iloc[0]

        st.subheader(
            f"Sample ID: {current_sample['sample_id']}"
        )

        st.markdown("#### Customer Message")
        st.info(current_sample["customer_message"])

        st.markdown("#### Human-Approved Expected Answer")
        st.write(current_sample["expected_answer"])

        if st.button(
            "Generate AI Answer",
            type="primary",
        ):
            with st.spinner("Generating the AI answer..."):
                try:
                    run_model_inference(
                        annotation_id=int(
                            current_sample["annotation_id"]
                        ),
                        customer_message=str(
                            current_sample["customer_message"]
                        ),
                    )

                except Exception as error:
                    st.error(f"Inference failed: {error}")

                else:
                    st.success(
                        "AI answer generated and saved."
                    )
                    st.rerun()

    st.divider()
    st.subheader("Saved Inference Results")

    inference_results = get_all_inference_results()

    if inference_results.empty:
        st.info("No inference results yet.")
    else:
        st.dataframe(
            inference_results,
            use_container_width=True,
            hide_index=True,
        )


# ---------------------------------------------------------
# Human evaluation
# ---------------------------------------------------------

def score_label(score: int, metric: str) -> str:
    """Create readable score descriptions."""

    descriptions = {
        "Correctness": {
            1: "1 — Incorrect",
            2: "2 — Mostly incorrect",
            3: "3 — Partially correct",
            4: "4 — Mostly correct",
            5: "5 — Completely correct",
        },
        "Relevance": {
            1: "1 — Irrelevant",
            2: "2 — Mostly irrelevant",
            3: "3 — Partially relevant",
            4: "4 — Mostly relevant",
            5: "5 — Fully relevant",
        },
        "Completeness": {
            1: "1 — Missing nearly everything",
            2: "2 — Major information missing",
            3: "3 — Some important information missing",
            4: "4 — Mostly complete",
            5: "5 — Fully complete",
        },
        "Helpfulness": {
            1: "1 — Not helpful",
            2: "2 — Slightly helpful",
            3: "3 — Moderately helpful",
            4: "4 — Helpful",
            5: "5 — Highly helpful",
        },
    }

    return descriptions[metric][score]


def show_human_evaluation() -> None:
    st.header("Human LLM Evaluation")

    completed_count, total_count = (
        get_human_evaluation_progress()
    )

    if total_count == 0:
        st.info(
            "Run at least one successful model inference first."
        )
        return

    st.write(
        f"Progress: {completed_count} of "
        f"{total_count} AI answers evaluated"
    )

    st.progress(completed_count / total_count)

    evaluation = get_next_pending_human_evaluation()

    if evaluation is None:
        st.success(
            "All successful AI answers have been evaluated."
        )

    else:
        st.subheader(
            f"Sample ID: {evaluation['sample_id']}"
        )

        st.markdown("#### Customer Message")
        st.info(evaluation["customer_message"])

        st.markdown("#### Human-Approved Expected Answer")
        st.write(evaluation["expected_answer"])

        st.markdown("#### AI-Generated Answer")
        st.write(evaluation["generated_answer"])

        col1, col2, col3 = st.columns(3)

        col1.metric(
            "Model",
            evaluation["model_name"],
        )

        col2.metric(
            "Latency",
            f"{evaluation['latency_seconds']:.3f} seconds",
        )

        col3.metric(
            "Total Tokens",
            evaluation["total_tokens"],
        )

        with st.form("human_evaluation_form"):
            correctness_score = st.selectbox(
                "Correctness",
                [1, 2, 3, 4, 5],
                index=4,
                format_func=lambda value: score_label(
                    value,
                    "Correctness",
                ),
            )

            relevance_score = st.selectbox(
                "Relevance",
                [1, 2, 3, 4, 5],
                index=4,
                format_func=lambda value: score_label(
                    value,
                    "Relevance",
                ),
            )

            completeness_score = st.selectbox(
                "Completeness",
                [1, 2, 3, 4, 5],
                index=4,
                format_func=lambda value: score_label(
                    value,
                    "Completeness",
                ),
            )

            helpfulness_score = st.selectbox(
                "Helpfulness",
                [1, 2, 3, 4, 5],
                index=4,
                format_func=lambda value: score_label(
                    value,
                    "Helpfulness",
                ),
            )

            hallucination_choice = st.radio(
                "Did the AI invent unsupported information?",
                ["No", "Yes"],
                horizontal=True,
            )

            evaluation_status = st.radio(
                "Final decision",
                ["Pass", "Fail"],
                horizontal=True,
            )

            evaluator_comments = st.text_area(
                "Evaluator comments",
                placeholder=(
                    "Explain missing steps, incorrect details, "
                    "or unsupported claims."
                ),
                height=120,
            )

            submitted = st.form_submit_button(
                "Save evaluation",
                type="primary",
            )

        if submitted:
            try:
                save_human_evaluation(
                    inference_id=evaluation["inference_id"],
                    correctness_score=correctness_score,
                    relevance_score=relevance_score,
                    completeness_score=completeness_score,
                    helpfulness_score=helpfulness_score,
                    hallucination_detected=(
                        hallucination_choice == "Yes"
                    ),
                    evaluation_status=evaluation_status,
                    evaluator_comments=evaluator_comments,
                )

            except Exception as error:
                st.error(
                    f"Could not save evaluation: {error}"
                )

            else:
                st.success("Evaluation saved.")
                st.rerun()

    st.divider()
    st.subheader("Completed Human Evaluations")

    evaluations = get_all_human_evaluations()

    if evaluations.empty:
        st.info("No completed evaluations.")

    else:
        display_data = evaluations.copy()

        display_data["hallucination_detected"] = (
            display_data["hallucination_detected"]
            .map({1: "Yes", 0: "No"})
        )

        st.dataframe(
            display_data,
            use_container_width=True,
            hide_index=True,
        )


def show_deepeval_workspace() -> None:
    """Run and save DeepEval results one sample at a time."""

    st.header("DeepEval Evaluation")

    completed_count, total_count = get_automated_evaluation_progress()

    if total_count == 0:
        st.info("Run at least one successful model inference first.")
        return

    st.write(
        f"Progress: {completed_count} of "
        f"{total_count} AI answers evaluated"
    )

    st.progress(completed_count / total_count)

    evaluation = get_next_pending_automated_evaluation()

    if evaluation is None:
        st.success(
            "All successful AI answers have been evaluated with DeepEval."
        )

    else:
        st.subheader(f"Sample ID: {evaluation['sample_id']}")

        st.markdown("#### Customer Message")
        st.info(evaluation["customer_message"])

        st.markdown("#### Human-Approved Expected Answer")
        st.write(evaluation["expected_answer"])

        st.markdown("#### GPT-Generated Answer")
        st.write(evaluation["generated_answer"])

        col1, col2, col3 = st.columns(3)

        col1.metric("Model", evaluation["model_name"])

        latency = evaluation["latency_seconds"]

        col2.metric(
            "Latency",
            f"{latency:.3f} seconds" if latency is not None else "N/A",
        )

        col3.metric(
            "Total Tokens",
            evaluation["total_tokens"]
            if evaluation["total_tokens"] is not None
            else "N/A",
        )

        if st.button("Run and Save DeepEval", type="primary"):
            with st.spinner("DeepEval is evaluating the response..."):
                try:
                    result = run_deepeval(
                        customer_message=str(
                            evaluation["customer_message"]
                        ),
                        expected_answer=str(
                            evaluation["expected_answer"]
                        ),
                        generated_answer=str(
                            evaluation["generated_answer"]
                        ),
                    )

                    save_automated_evaluation(
                        inference_id=int(evaluation["inference_id"]),
                        correctness_score=result["correctness"]["score"],
                        relevance_score=result["relevance"]["score"],
                        completeness_score=result["completeness"]["score"],
                        helpfulness_score=result["helpfulness"]["score"],
                        overall_score=result["overall_score"],
                        evaluation_status=result["status"],
                        correctness_reason=result["correctness"]["reason"],
                        relevance_reason=result["relevance"]["reason"],
                        completeness_reason=result["completeness"]["reason"],
                        helpfulness_reason=result["helpfulness"]["reason"],
                    )

                except Exception as error:
                    st.error(f"DeepEval could not be completed: {error}")

                else:
                    st.success("DeepEval result saved successfully.")
                    st.rerun()

    st.divider()
    st.subheader("Completed DeepEval Results")

    results = get_all_automated_evaluations()

    if results.empty:
        st.info("No DeepEval results have been saved yet.")

    else:
        summary_columns = [
            "sample_id",
            "model_name",
            "correctness_score",
            "relevance_score",
            "completeness_score",
            "helpfulness_score",
            "overall_score",
            "evaluation_status",
            "latency_seconds",
            "total_tokens",
            "evaluated_at",
        ]

        st.dataframe(
            results[summary_columns],
            use_container_width=True,
            hide_index=True,
        )
        # ---------------------------------------------------------
# Main application
# ---------------------------------------------------------
def create_fine_tuning_jsonl(
    gold_dataset: pd.DataFrame,
) -> str:
    """
    Convert reviewer-approved Gold Dataset records
    into JSONL chat-training examples.
    """

    system_instruction = (
        "You are a professional customer-support assistant. "
        "Respond clearly, empathetically, and concisely. "
        "Provide practical next steps. "
        "Do not claim that you completed refunds, cancellations, "
        "account changes, or other external actions unless confirmed."
    )

    jsonl_lines: list[str] = []

    for row in gold_dataset.itertuples(index=False):
        customer_message = str(row.customer_message).strip()
        expected_answer = str(row.expected_answer).strip()

        if not customer_message or not expected_answer:
            continue

        training_example = {
            "messages": [
                {
                    "role": "system",
                    "content": system_instruction,
                },
                {
                    "role": "user",
                    "content": customer_message,
                },
                {
                    "role": "assistant",
                    "content": expected_answer,
                },
            ]
        }

        jsonl_lines.append(
            json.dumps(training_example, ensure_ascii=False)
        )

    return "\n".join(jsonl_lines)

def validate_fine_tuning_jsonl(
    jsonl_content: str,
) -> tuple[list[str], int]:
    """
    Validate fine-tuning JSONL content.

    Returns:
        errors: List of validation problems.
        valid_example_count: Number of valid examples.
    """

    errors: list[str] = []
    valid_example_count = 0

    if not jsonl_content.strip():
        return ["The JSONL file is empty."], 0

    lines = jsonl_content.splitlines()

    for line_number, line in enumerate(lines, start=1):
        try:
            example = json.loads(line)

        except json.JSONDecodeError as error:
            errors.append(
                f"Line {line_number}: Invalid JSON — {error}"
            )
            continue

        messages = example.get("messages")

        if not isinstance(messages, list):
            errors.append(
                f"Line {line_number}: 'messages' must be a list."
            )
            continue

        roles = {
            message.get("role")
            for message in messages
            if isinstance(message, dict)
        }

        required_roles = {"system", "user", "assistant"}
        missing_roles = required_roles - roles

        if missing_roles:
            errors.append(
                f"Line {line_number}: Missing role(s): "
                + ", ".join(sorted(missing_roles))
            )
            continue

        line_has_error = False

        for message_index, message in enumerate(
            messages,
            start=1,
        ):
            if not isinstance(message, dict):
                errors.append(
                    f"Line {line_number}, message "
                    f"{message_index}: Must be an object."
                )
                line_has_error = True
                continue

            role = message.get("role")
            content = message.get("content")

            if role not in {
                "system",
                "user",
                "assistant",
            }:
                errors.append(
                    f"Line {line_number}: Unsupported role "
                    f"'{role}'."
                )
                line_has_error = True

            if not isinstance(content, str) or not content.strip():
                errors.append(
                    f"Line {line_number}, role '{role}': "
                    "Content cannot be empty."
                )
                line_has_error = True

        if not line_has_error:
            valid_example_count += 1

    if valid_example_count < 10:
        errors.append(
            "At least 10 valid training examples are required. "
            f"Found {valid_example_count}."
        )

    return errors, valid_example_count

def main() -> None:
    st.set_page_config(
        page_title="AI DataOps Platform",
        page_icon="📊",
        layout="wide",
    )

    initialize_database()

    st.title("AI DataOps & Model Evaluation Platform")

    (
        ingestion_tab,
        annotation_tab,
        review_tab,
        gold_tab,
        inference_tab,
        human_evaluation_tab,
        deepeval_tab,
        fine_tuning_tab,
    ) = st.tabs(
        [
            "Dataset Ingestion",
            "Annotation Workspace",
            "Reviewer Workspace",
            "Gold Dataset",
            "Model Inference",
            "Human Evaluation",
            "DeepEval Evaluation",
            "Fine Tuning",
        ]
    )

    with ingestion_tab:
        show_dataset_ingestion()

    with fine_tuning_tab:
        show_fine_tuning()

    with annotation_tab:
        show_annotation_workspace()

    with review_tab:
        show_reviewer_workspace()

    with gold_tab:
        show_gold_dataset()

    with inference_tab:
        show_model_inference()

    with human_evaluation_tab:
        show_human_evaluation()

    with deepeval_tab:
        show_deepeval_workspace()


if __name__ == "__main__":
    main()