import os


from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCase, LLMTestCaseParams

os.environ["OPENAI_API_KEY"] = ""

def evaluate_metric(metric, test_case):
    """
    Runs one DeepEval metric safely.
    Returns the score and reason.
    """

    try:
        metric.measure(test_case)

        return {
            "score": round(metric.score, 2),
            "reason": metric.reason,
        }

    except Exception as error:

        return {
            "score": 0.0,
            "reason": str(error),
        }


def run_deepeval(
    customer_message: str,
    expected_answer: str,
    generated_answer: str,
):

    test_case = LLMTestCase(
        input=customer_message,
        actual_output=generated_answer,
        expected_output=expected_answer,
    )

    # -------------------------------------------------
    # Correctness
    # -------------------------------------------------

    correctness_metric = GEval(
        name="Correctness",
        criteria="""
Evaluate whether the AI response is factually correct and
matches the expected answer.

Score between 0 and 1.

1 = completely correct.

0 = completely incorrect.
""",
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.EXPECTED_OUTPUT,
        ],
    )

    # -------------------------------------------------
    # Relevance
    # -------------------------------------------------

    relevance_metric = GEval(
        name="Relevance",
        criteria="""
Evaluate whether the generated answer directly answers
the customer's question.

Score between 0 and 1.
""",
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
        ],
    )

    # -------------------------------------------------
    # Completeness
    # -------------------------------------------------

    completeness_metric = GEval(
        name="Completeness",
        criteria="""
Evaluate whether the generated answer contains all
important information required to solve the customer's
problem.

Score between 0 and 1.
""",
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.EXPECTED_OUTPUT,
        ],
    )

    # -------------------------------------------------
    # Helpfulness
    # -------------------------------------------------

    helpfulness_metric = GEval(
        name="Helpfulness",
        criteria="""
Evaluate whether the generated answer is clear,
actionable and useful.

Score between 0 and 1.
""",
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
        ],
    )

    # -------------------------------------------------
    # Execute Metrics
    # -------------------------------------------------

    correctness = evaluate_metric(
        correctness_metric,
        test_case,
    )

    relevance = evaluate_metric(
        relevance_metric,
        test_case,
    )

    completeness = evaluate_metric(
        completeness_metric,
        test_case,
    )

    helpfulness = evaluate_metric(
        helpfulness_metric,
        test_case,
    )

    # -------------------------------------------------
    # Overall Score
    # -------------------------------------------------

    overall_score = round(
        (
            correctness["score"]
            + relevance["score"]
            + completeness["score"]
            + helpfulness["score"]
        )
        / 4,
        2,
    )

    status = (
        "Pass"
        if overall_score >= 0.80
        else "Fail"
    )

    return {
        "correctness": correctness,
        "relevance": relevance,
        "completeness": completeness,
        "helpfulness": helpfulness,
        "overall_score": overall_score,
        "status": status,
    }