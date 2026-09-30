"""Exercise 3.4 (bonus) — score the same 20 traces with RAGAS and DeepEval.

Not part of the required lab code. It needs extra packages that are
deliberately kept out of requirements.txt:

    pip install ragas deepeval langchain-openai python-dotenv

Run from the repo root:  python artifacts/framework_comparison.py
Both frameworks read the same inputs (question, actual answer, retrieved
chunks, expected answer) and use the same judge model.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

JUDGE_MODEL = "gpt-4o-mini"
OUTPUT = ROOT / "artifacts" / "framework_comparison.json"
METRICS = ("faithfulness", "answer_relevancy", "context_recall", "context_precision")


def load_cases() -> list[dict[str, Any]]:
    golden = json.loads((ROOT / "golden_dataset.json").read_text(encoding="utf-8"))
    actual = json.loads(
        (ROOT / "artifacts" / "actual_answers.json").read_text(encoding="utf-8")
    )
    answers = {record["id"]: record for record in actual["answers"]}
    return [
        {
            "id": pair["id"],
            "question": pair["question"],
            "expected": pair["expected_answer"],
            "answer": answers[pair["id"]]["actual_answer"],
            "contexts": [
                chunk["text"] for chunk in answers[pair["id"]]["retrieved_contexts"]
            ],
        }
        for pair in golden["qa_pairs"]
    ]


def run_ragas(cases: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import EvaluationDataset, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import (
        Faithfulness,
        LLMContextPrecisionWithReference,
        LLMContextRecall,
        ResponseRelevancy,
    )

    dataset = EvaluationDataset.from_list(
        [
            {
                "user_input": case["question"],
                "response": case["answer"],
                "retrieved_contexts": case["contexts"],
                "reference": case["expected"],
            }
            for case in cases
        ]
    )
    result = evaluate(
        dataset,
        metrics=[
            Faithfulness(),
            ResponseRelevancy(),
            LLMContextRecall(),
            LLMContextPrecisionWithReference(),
        ],
        llm=LangchainLLMWrapper(ChatOpenAI(model=JUDGE_MODEL, temperature=0)),
        embeddings=LangchainEmbeddingsWrapper(
            OpenAIEmbeddings(model="text-embedding-3-small")
        ),
    )
    frame = result.to_pandas()
    columns = {
        "faithfulness": "faithfulness",
        "answer_relevancy": "answer_relevancy",
        "context_recall": "context_recall",
        "context_precision": "llm_context_precision_with_reference",
    }
    scores: dict[str, dict[str, float | None]] = {}
    for case, (_, row) in zip(cases, frame.iterrows()):
        scores[case["id"]] = {
            name: _clean(row.get(column)) for name, column in columns.items()
        }
    return scores


def run_deepeval(cases: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    from deepeval.metrics import (
        AnswerRelevancyMetric,
        ContextualPrecisionMetric,
        ContextualRecallMetric,
        FaithfulnessMetric,
    )
    from deepeval.test_case import LLMTestCase

    metric_types = {
        "faithfulness": FaithfulnessMetric,
        "answer_relevancy": AnswerRelevancyMetric,
        "context_recall": ContextualRecallMetric,
        "context_precision": ContextualPrecisionMetric,
    }
    scores: dict[str, dict[str, float | None]] = {}
    for case in cases:
        test_case = LLMTestCase(
            input=case["question"],
            actual_output=case["answer"],
            expected_output=case["expected"],
            retrieval_context=case["contexts"],
        )
        row: dict[str, float | None] = {}
        for name, metric_type in metric_types.items():
            metric = metric_type(model=JUDGE_MODEL, include_reason=False, async_mode=False)
            try:
                metric.measure(test_case, _show_indicator=False)
                row[name] = _clean(metric.score)
            except Exception as exc:  # one failed judge call must not lose the run
                print(f"  deepeval {case['id']} {name} failed: {exc}")
                row[name] = None
        scores[case["id"]] = row
        print(f"  deepeval {case['id']} done", flush=True)
    return scores


def _clean(value: Any) -> float | None:
    """Convert a framework score to a rounded float; NaN/None become None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else round(number, 3)


def _average(scores: dict[str, dict[str, float | None]], metric: str) -> float | None:
    values = [row[metric] for row in scores.values() if row.get(metric) is not None]
    return round(sum(values) / len(values), 3) if values else None


def main() -> None:
    cases = load_cases()
    saved: dict[str, Any] = (
        json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
    )
    # Each framework's scores are saved as soon as they exist, so a crash in
    # the second framework does not force paying for the first one again.
    for name, runner in (("ragas", run_ragas), ("deepeval", run_deepeval)):
        if name not in saved:
            print(f"Running {name} on {len(cases)} cases...", flush=True)
            saved[name] = runner(cases)
            OUTPUT.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")

    saved["judge_model"] = JUDGE_MODEL
    saved["averages"] = {
        name: {metric: _average(saved[name], metric) for metric in METRICS}
        for name in ("ragas", "deepeval")
    }
    OUTPUT.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(saved["averages"], indent=2))
    print(f"Saved: {OUTPUT}")


if __name__ == "__main__":
    main()
