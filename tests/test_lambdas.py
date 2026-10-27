"""Unit tests for the pipeline Lambdas and the evaluation script.

AWS clients are replaced with fakes, so these run offline with no credentials:
    pip install pytest && pytest -q
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, relpath, env, monkeypatch, fake_client=None):
    """Import a Lambda module from its folder with env vars and a fake boto3."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    fake_boto3 = types.SimpleNamespace(client=lambda *a, **k: fake_client or types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- chunking

@pytest.fixture
def chunking(monkeypatch):
    return load("chunking", "lambda/chunking/main.py", {"OUTPUT_BUCKET": "out"}, monkeypatch)


def test_chunks_cover_all_text_with_overlap(chunking):
    pages = [(1, "Alpha sentence one. " * 40), (2, "Beta sentence two. " * 40)]
    chunks = chunking.chunk_pages(pages, size=500, overlap=50)
    assert len(chunks) > 2
    assert all(len(c["text"]) <= 500 for c in chunks)
    joined = " ".join(c["text"] for c in chunks)
    assert "Alpha" in joined and "Beta" in joined


def test_chunk_page_ranges(chunking):
    pages = [(1, "a " * 300), (2, "b " * 300), (3, "c " * 300)]
    chunks = chunking.chunk_pages(pages, size=400, overlap=40)
    assert chunks[0]["page_start"] == 1
    assert chunks[-1]["page_end"] == 3
    assert all(c["page_start"] <= c["page_end"] for c in chunks)


def test_chunking_rejects_overlap_not_smaller_than_size(chunking):
    with pytest.raises(ValueError):
        chunking.chunk_pages([(1, "text")], size=100, overlap=100)


def test_empty_document_gives_no_chunks(chunking):
    assert chunking.chunk_pages([], size=100, overlap=10) == []


def test_safe_name(chunking):
    assert chunking.safe_name("documents/My File (1).pdf") == "documents_My_File_1_.pdf"


# ---------------------------------------------------------------- authorizer

@pytest.fixture
def authorizer(monkeypatch):
    return load("authorizer", "lambda/authorizer/main.py", {"API_KEY": "s3cret"}, monkeypatch)


@pytest.mark.parametrize("headers,expected", [
    ({"x-api-key": "s3cret"}, True),
    ({"X-Api-Key": "s3cret"}, True),
    ({"x-api-key": "wrong"}, False),
    ({"x-api-key": ""}, False),
    ({}, False),
    (None, False),
])
def test_authorizer(authorizer, headers, expected):
    assert authorizer.lambda_handler({"headers": headers}, None) == {"isAuthorized": expected}


# ---------------------------------------------------------------- query

class FakeBedrock:
    def __init__(self):
        self.calls = []

    def retrieve_and_generate(self, **kwargs):
        self.calls.append(kwargs)
        return {"output": {"text": "The answer."}, "citations": [{"retrievedReferences": [{}]}]}


def query_module(monkeypatch, guardrail=""):
    fake = FakeBedrock()
    env = {"KNOWLEDGE_BASE_ID": "kb1", "MODEL_ID": "amazon.titan-text-premier-v1:0",
           "GUARDRAIL_ID": guardrail, "GUARDRAIL_VERSION": "1" if guardrail else ""}
    return load("query", "lambda/query/main.py", env, monkeypatch, fake), fake


def test_query_requires_question(monkeypatch):
    query, _ = query_module(monkeypatch)
    assert query.lambda_handler({"body": "{}"}, None)["statusCode"] == 400


def test_query_rejects_bad_json(monkeypatch):
    query, _ = query_module(monkeypatch)
    assert query.lambda_handler({"body": "not json"}, None)["statusCode"] == 400


def test_query_success_without_guardrail(monkeypatch):
    query, fake = query_module(monkeypatch)
    resp = query.lambda_handler({"body": json.dumps({"query": "hi"})}, None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["answer"] == "The answer."
    kb = fake.calls[0]["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]
    assert "generationConfiguration" not in kb
    assert kb["modelArn"].endswith("foundation-model/amazon.titan-text-premier-v1:0")


def test_query_applies_guardrail(monkeypatch):
    query, fake = query_module(monkeypatch, guardrail="gr123")
    query.lambda_handler({"body": json.dumps({"query": "hi"})}, None)
    kb = fake.calls[0]["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]
    assert kb["generationConfiguration"]["guardrailConfiguration"] == {"guardrailId": "gr123", "guardrailVersion": "1"}


# ---------------------------------------------------------------- eval

@pytest.fixture
def run_eval():
    spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval/run_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_eval_scores_keywords_and_citations(run_eval):
    citations = [{"retrievedReferences": [{"metadata": {"source_document": "s3://raw/documents/sample.pdf"}}]}]
    result = run_eval.score({"expected_keywords": ["alpha", "beta"], "expected_source": "sample.pdf"},
                            "Alpha and beta", citations)
    assert result["passed"] and result["keyword_recall"] == 1.0 and result["citation_hit"]


def test_eval_abstention(run_eval):
    assert run_eval.score({"expect_no_answer": True}, "Sorry, I am unable to assist.", [])["passed"]
    assert not run_eval.score({"expect_no_answer": True}, "The capital is Paris.", [])["passed"]


def test_eval_parses_judge_output(run_eval):
    parsed = run_eval.parse_judgement('Sure: {"faithfulness": 5, "relevance": 4, "reason": "ok"}')
    assert parsed == {"faithfulness": 5, "relevance": 4, "judge_reason": "ok"}
    assert "judge_error" in run_eval.parse_judgement("no json here")


# ---------------------------------------------------------------- online eval

class FakeAws:
    """One fake standing in for both the S3 and Bedrock clients."""

    def __init__(self, samples=None, judge_text='{"faithfulness": 5, "relevance": 4, "reason": "ok"}'):
        self.objects = dict(samples or {})
        self.judge_text = judge_text
        self.converse_calls = 0

    # bedrock-agent-runtime
    def retrieve_and_generate(self, **kwargs):
        return {"output": {"text": "The answer."},
                "citations": [{"retrievedReferences": [{"content": {"text": "Passage A"}}]}]}

    # s3
    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        body = self.objects[Key]
        return {"Body": types.SimpleNamespace(read=lambda: body)}

    def get_paginator(self, name):
        objects = self.objects

        class Paginator:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k} for k in objects if k.startswith(Prefix)]}
        return Paginator()

    # bedrock-runtime
    def converse(self, **kwargs):
        self.converse_calls += 1
        return {"output": {"message": {"content": [{"text": self.judge_text}]}}}


def test_query_saves_eval_sample_when_sampled(monkeypatch):
    fake = FakeAws()
    query = load("query", "lambda/query/main.py",
                 {"KNOWLEDGE_BASE_ID": "kb1", "MODEL_ID": "m", "GUARDRAIL_ID": "",
                  "EVAL_SAMPLE_BUCKET": "samples-bucket", "EVAL_SAMPLE_RATE": "1"}, monkeypatch, fake)
    ctx = types.SimpleNamespace(aws_request_id="req-123")
    assert query.lambda_handler({"body": json.dumps({"query": "What is X?"})}, ctx)["statusCode"] == 200
    [key] = [k for k in fake.objects if k.startswith("samples/")]
    record = json.loads(fake.objects[key])
    assert key.endswith("/req-123.json")
    assert record["question"] == "What is X?" and record["passages"] == ["Passage A"]


def test_query_skips_sampling_when_rate_zero(monkeypatch):
    fake = FakeAws()
    query = load("query", "lambda/query/main.py",
                 {"KNOWLEDGE_BASE_ID": "kb1", "MODEL_ID": "m", "GUARDRAIL_ID": "",
                  "EVAL_SAMPLE_BUCKET": "samples-bucket", "EVAL_SAMPLE_RATE": "0"}, monkeypatch, fake)
    query.lambda_handler({"body": json.dumps({"query": "q"})}, types.SimpleNamespace(aws_request_id="r"))
    assert fake.objects == {}


def test_sampling_failure_never_breaks_the_answer(monkeypatch):
    fake = FakeAws()
    fake.put_object = lambda **kw: (_ for _ in ()).throw(RuntimeError("s3 down"))
    query = load("query", "lambda/query/main.py",
                 {"KNOWLEDGE_BASE_ID": "kb1", "MODEL_ID": "m", "GUARDRAIL_ID": "",
                  "EVAL_SAMPLE_BUCKET": "b", "EVAL_SAMPLE_RATE": "1"}, monkeypatch, fake)
    resp = query.lambda_handler({"body": json.dumps({"query": "q"})}, types.SimpleNamespace(aws_request_id="r"))
    assert resp["statusCode"] == 200


def judge_module(monkeypatch, fake):
    return load("judge", "lambda/judge/main.py",
                {"EVAL_SAMPLE_BUCKET": "b", "JUDGE_MODEL_ID": "judge", "METRIC_NAMESPACE": "RAG/test",
                 "PROJECT_NAME": "test-dev", "MAX_SAMPLES_PER_RUN": "200"}, monkeypatch, fake)


def sample(rid):
    return json.dumps({"request_id": rid, "question": "q", "answer": "a", "passages": ["p"]}).encode()


def test_judge_grades_day_and_writes_report(monkeypatch, capsys):
    fake = FakeAws(samples={"samples/2026/10/26/r1.json": sample("r1"),
                            "samples/2026/10/26/r2.json": sample("r2"),
                            "samples/2026/10/27/other.json": sample("x")})
    judge = judge_module(monkeypatch, fake)
    summary = judge.lambda_handler({"date": "2026-10-26"}, None)
    assert summary["graded"] == 2 and summary["avg_faithfulness"] == 5 and summary["avg_relevance"] == 4
    assert fake.converse_calls == 2
    report = json.loads(fake.objects["reports/2026/10/26.json"])
    assert report["summary"]["low_faithfulness"] == 0
    emf = [json.loads(line) for line in capsys.readouterr().out.splitlines() if '"_aws"' in line]
    assert len(emf) == 2 and emf[0]["Faithfulness"] == 5 and emf[0]["Project"] == "test-dev"


def test_judge_counts_bad_judge_output_as_error(monkeypatch):
    fake = FakeAws(samples={"samples/2026/10/26/r1.json": sample("r1")}, judge_text="no idea")
    summary = judge_module(monkeypatch, fake).lambda_handler({"date": "2026-10-26"}, None)
    assert summary["graded"] == 0 and summary["judge_errors"] == 1


def test_judge_flags_low_faithfulness(monkeypatch):
    fake = FakeAws(samples={"samples/2026/10/26/r1.json": sample("r1")},
                   judge_text='{"faithfulness": 2, "relevance": 5, "reason": "made up a date"}')
    summary = judge_module(monkeypatch, fake).lambda_handler({"date": "2026-10-26"}, None)
    assert summary["low_faithfulness"] == 1
    report = json.loads(fake.objects["reports/2026/10/26.json"])
    assert report["lowest_scoring"][0]["reason"] == "made up a date"


def test_judge_rejects_out_of_range_scores(monkeypatch):
    judge = judge_module(monkeypatch, FakeAws())
    with pytest.raises(ValueError):
        judge.parse_judgement('{"faithfulness": 9, "relevance": 1}')
