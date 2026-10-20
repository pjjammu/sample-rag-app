"""Tests for judge_v2.py and validate_judge.py (no network). Run: pytest -q"""
import csv, hashlib, json, types
import pytest
import judge_v2
import validate_judge as vj
import review_answers as ra

HEADER = ["id", "type", "question", "reference_answer", "model_answer", "cited_pages", "expected_pages",
          "auto_or_judge_verdict", "judge_reason", "my_verdict"]


def make_dataset(tmp_path, n=12, human=None):
    evals = tmp_path / "evals"
    (evals / "golden_set").mkdir(parents=True)
    (evals / "baseline").mkdir()
    (evals / "judge").mkdir()
    golden, graded, planted = [], [], []
    for k in range(n):
        i = f"G{k:02d}"
        golden.append({"id": i, "type": "lookup", "answerable": True, "question": f"Question {i}?", "expected_pages": [1, 2],
                       "key_phrase": "x", "alt_phrases": [], "reference_answer": f"Reference {i}"})
        graded.append([i, "lookup", f"Question {i}?", f"Reference {i}", f"Answer {i}: FACT", "1", "1,2", "CORRECT", "", (human or {}).get(i, "CORRECT")])
        planted.append({"id": i, "find": "FACT", "replace": "WRONG", "error": "fact changed"})
    gp = evals / "golden_set" / "golden_set.jsonl"
    gp.write_text("\n".join(json.dumps(g) for g in golden) + "\n", encoding="utf-8")
    (evals / "golden_set" / "golden_set.sha256").write_text(hashlib.sha256(gp.read_bytes()).hexdigest() + "  golden_set.jsonl\n")
    with open(evals / "baseline" / "B0_generation_graded.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f); w.writerow(HEADER); w.writerows(graded)
    (evals / "judge" / "planted_errors.jsonl").write_text("\n".join(json.dumps(p) for p in planted[:10]) + "\n", encoding="utf-8")
    return evals


class FakeJudge:
    """Says INCORRECT when the model answer contains WRONG, otherwise CORRECT."""
    def __init__(self, always=None):
        self.always, self.calls = always, []
        self.messages = types.SimpleNamespace(create=self._c)
    def _c(self, **kw):
        self.calls.append(kw)
        content = kw["messages"][0]["content"]
        answer = content.split("<model_answer>")[1]
        verdict = self.always or ("INCORRECT" if "WRONG" in answer else "CORRECT")
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=json.dumps({"verdict": verdict, "reason": "fake"}))],
                                     stop_reason="end_turn")


PAGE = lambda p: f"text of page {p}"


# ---------- judge_v2 ----------
def test_prompt_contains_pages_reference_and_answer_as_data():
    p = judge_v2.build_prompt("Q?", "REF", "ANS", {2: "second", 1: "first"})
    assert p.index('<page number="1">') < p.index('<page number="2">')
    assert "<reference_answer>REF</reference_answer>" in p and "<model_answer>ANS</model_answer>" in p


def test_system_prompt_forbids_outside_knowledge_and_asks_for_json_only():
    s = judge_v2.JUDGE_SYSTEM
    assert "Do not use your own knowledge" in s and "Your entire reply must be this JSON object" in s


# ---------- helpers ----------
def test_plant_applies_one_replacement_and_validates():
    graded = {"A": {"my_verdict": "CORRECT", "model_answer": "five FACT FACT"}}
    out = vj.plant(graded, [{"id": "A", "find": "FACT", "replace": "WRONG", "error": "e"}])
    assert out[0][1] == "five WRONG FACT"
    with pytest.raises(ValueError):
        vj.plant(graded, [{"id": "A", "find": "nope", "replace": "x", "error": "e"}])
    graded["A"]["my_verdict"] = "PARTIAL"
    with pytest.raises(ValueError):
        vj.plant(graded, [{"id": "A", "find": "FACT", "replace": "x", "error": "e"}])


def test_confusion_matrix():
    m = vj.confusion({"a": "CORRECT", "b": "CORRECT", "c": "INCORRECT"}, {"a": "CORRECT", "b": "PARTIAL", "c": "UNPARSEABLE"})
    assert m["CORRECT"] == {"CORRECT": 1, "PARTIAL": 1} and m["INCORRECT"] == {"OTHER": 1}


def test_summarize_pass_and_each_failure_mode():
    human = {f"q{i}": "CORRECT" for i in range(10)}
    ok = vj.summarize(human, dict(human), {f"q{i}": "CORRECT" for i in range(10)}, {f"q{i}": "INCORRECT" for i in range(10)})
    assert ok["passed"] and ok["agree"] == 10
    # too many correct originals failed
    bad = vj.summarize(human, dict(human), {f"q{i}": ("PARTIAL" if i < 2 else "CORRECT") for i in range(10)}, {f"q{i}": "INCORRECT" for i in range(10)})
    assert not bad["passed"] and len(bad["orig_fails"]) == 2
    # too few planted errors caught
    weak = vj.summarize(human, dict(human), {f"q{i}": "CORRECT" for i in range(10)}, {f"q{i}": ("CORRECT" if i < 3 else "INCORRECT") for i in range(10)})
    assert not weak["passed"] and len(weak["caught"]) == 7
    # judge passes a wrong answer
    h2 = {**human, "q0": "INCORRECT"}
    miss = vj.summarize(h2, {**human}, {}, {})
    assert miss["missed_incorrect"] == ["q0"] and not miss["passed"]


# ---------- end to end ----------
def test_run_passes_with_a_good_judge_and_logs_the_attempt(tmp_path):
    evals = make_dataset(tmp_path)
    vj.main(["--evals-dir", str(evals)], client=FakeJudge(), page_text_fn=PAGE)
    log = [json.loads(l) for l in (evals / "judge" / "attempts.jsonl").read_text().splitlines()]
    assert len(log) == 1 and log[0]["passed"] is True and log[0]["agreement"] == "12/12" and log[0]["planted_caught"] == "10/10"
    assert any(p.name.startswith("attempt1_") and p.suffix == ".md" for p in (evals / "judge").iterdir())


def test_a_lenient_judge_fails_the_planted_error_test(tmp_path):
    evals = make_dataset(tmp_path)
    vj.main(["--evals-dir", str(evals)], client=FakeJudge(always="CORRECT"), page_text_fn=PAGE)
    log = json.loads((evals / "judge" / "attempts.jsonl").read_text().splitlines()[0])
    assert log["passed"] is False and log["planted_caught"] == "0/10"


def test_judge_sees_the_expected_pages(tmp_path):
    evals = make_dataset(tmp_path, n=10)
    client = FakeJudge()
    vj.main(["--evals-dir", str(evals), "--limit", "2"], client=client, page_text_fn=PAGE)
    assert 'text of page 1' in client.calls[0]["messages"][0]["content"] and 'text of page 2' in client.calls[0]["messages"][0]["content"]


def test_smoke_run_is_not_logged(tmp_path):
    evals = make_dataset(tmp_path)
    vj.main(["--evals-dir", str(evals), "--limit", "3"], client=FakeJudge(), page_text_fn=PAGE)
    assert not (evals / "judge" / "attempts.jsonl").exists()


def test_stops_after_three_logged_attempts(tmp_path):
    evals = make_dataset(tmp_path)
    (evals / "judge" / "attempts.jsonl").write_text("{}\n{}\n{}\n")
    with pytest.raises(SystemExit):
        vj.main(["--evals-dir", str(evals)], client=FakeJudge(), page_text_fn=PAGE)


def test_stops_if_golden_set_changed(tmp_path):
    evals = make_dataset(tmp_path)
    p = evals / "golden_set" / "golden_set.jsonl"
    p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        vj.main(["--evals-dir", str(evals)], client=FakeJudge(), page_text_fn=PAGE)


def test_clean_page_removes_markers_page_number_and_running_header():
    raw = "109\nLaws of the Game 2026/27 | Law 11 | Offside\n\x07A player is offside\n\u2022 \x07if nearer\tto the goal"
    out = judge_v2.clean_page(raw)
    assert "\x07" not in out and "Laws of the Game 2026/27" not in out and not out.startswith("109")
    assert "A player is offside" in out and "if nearer to the goal" in out
