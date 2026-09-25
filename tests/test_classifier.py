import copy

from paperless_jev.classifier import NONE, build_request, interpret, plan, prepare_text, render_title
from paperless_jev.config import DEFAULTS
from paperless_jev.paperless import Metadata


def make_meta() -> Metadata:
    return Metadata(
        correspondents={1: "Swisscom", 2: "Stadtwerke"},
        document_types={10: "Rechnung", 11: "Vertrag", 12: "Rechnung"},
        storage_paths={},
        tags={100: "Posteingang", 101: "Steuern", 102: "ai-review", 103: "Auto"},
        inbox_tags={100},
    )


def make_doc() -> dict:
    return {
        "id": 42,
        "title": "scan_0042",
        "content": "Swisscom\nRechnung vom 12.03.2026\nZahlbar bis 11.04.2026\nTotal CHF 84.20",
        "original_file_name": "scan_0042.pdf",
        "correspondent": None,
        "document_type": None,
        "storage_path": None,
        "created": "2026-03-20",
        "tags": [100, 103],
    }


def cfg(**overrides) -> dict:
    c = copy.deepcopy(DEFAULTS)
    c.update(overrides)
    return c


def test_build_request_questions():
    descriptions = {("document_type", 11): {"text": "", "active": False}}
    req = build_request(make_doc(), make_meta(), descriptions, cfg())

    assert set(req.questions) == {"document_type", "correspondent", "created", "tag:101"}
    dt = req.questions["document_type"]
    # doppelte Namen werden eindeutig, deaktivierte fehlen, NONE ist immer dabei
    assert set(dt["criteria"]) == {"Rechnung #10", "Rechnung #12", NONE}
    assert req.options["created"]["12.03.2026"] == "2026-03-12"
    # Posteingang, Status-Tags und bereits gesetzte Tags werden nicht gefragt
    assert "tag:100" not in req.questions and "tag:102" not in req.questions and "tag:103" not in req.questions


def jev_response() -> dict:
    return {
        "model": "jev-1.13.0",
        "usage": {"input_tokens": 500},
        "answers": {
            "document_type": {"type": "choice", "choice": "Rechnung #10", "confidence": 0.95,
                              "probabilities": {"Rechnung #10": 0.97, "Rechnung #12": 0.03, NONE: 0.0}},
            "correspondent": {"type": "choice", "choice": "Swisscom", "confidence": 0.6,
                              "probabilities": {"Swisscom": 0.8, "Stadtwerke": 0.2, NONE: 0.0}},
            "created": {"type": "choice", "choice": "12.03.2026", "confidence": 0.97,
                        "probabilities": {"12.03.2026": 0.98, "11.04.2026": 0.02}},
            "tag:101": {"type": "noul", "noul": 0.7},
        },
    }


def test_interpret_and_plan_auto_vs_review():
    c = cfg()
    meta = make_meta()
    doc = make_doc()
    req = build_request(doc, meta, {}, c)
    result = interpret(jev_response(), req, meta, c)

    assert result["fields"]["document_type"] == {
        "value": 10, "label": "Rechnung #10", "confidence": 0.95, "level": "auto",
        "top": [["Rechnung #10", 0.97], ["Rechnung #12", 0.03], [NONE, 0.0]],
    }
    assert result["fields"]["correspondent"]["level"] == "suggest"
    assert result["tags"] == [{"id": 101, "label": "Steuern", "p": 0.7, "level": "suggest"}]

    decision = plan(result, doc, c)
    assert decision["updates"] == {"document_type": 10, "created": "2026-03-12", "tags_add": []}
    assert decision["needs_review"] is True


def test_plan_respects_existing_values():
    c = cfg()
    meta = make_meta()
    doc = make_doc() | {"correspondent": 2}
    req = build_request(doc, meta, {}, c)
    result = interpret(jev_response(), req, meta, c)
    result["tags"] = []
    decision = plan(result, doc, c)
    assert "correspondent" not in decision["updates"]
    assert decision["needs_review"] is False


def test_none_choice_is_not_a_value():
    c = cfg()
    meta = make_meta()
    req = build_request(make_doc(), meta, {}, c)
    resp = jev_response()
    resp["answers"]["document_type"] = {"type": "choice", "choice": NONE, "confidence": 0.99, "probabilities": {}}
    result = interpret(resp, req, meta, c)
    assert result["fields"]["document_type"]["value"] is None
    assert result["fields"]["document_type"]["level"] == "none"


def test_prepare_text_truncates_head_and_tail():
    text = "A" * 900 + "\n" + "B" * 900
    out = prepare_text(text, 1000)
    assert out.startswith("A" * 700) and out.endswith("B" * 300) and "[...]" in out


def test_render_title():
    tpl = "{document_type} {correspondent} {created:%Y-%m}"
    assert render_title(tpl, {"document_type": "Rechnung", "correspondent": "Swisscom", "created": "2026-03-12"}) == "Rechnung Swisscom 2026-03"
    assert render_title(tpl, {"document_type": "Rechnung", "correspondent": None, "created": "2026-03-12"}) is None


def test_instance_host_header():
    from paperless_jev.config import Instance

    inst = Instance(1, "p", "http://paperless:8000", "https://paperless.example.com", "t", True)
    assert inst.host_header == "paperless.example.com"
    assert Instance(1, "p", "https://paperless.example.com", "", "t", True).host_header is None


def test_examples_become_structured_criteria():
    examples = {("document_type", 10): ["Gutschriftsanzeige", "Belastungsanzeige"]}
    descriptions = {("document_type", 10): {"text": "Bank documents", "active": True}}
    req = build_request(make_doc(), make_meta(), descriptions, cfg(), examples=examples)
    q = req.questions["document_type"]
    assert q["criteria"]["Rechnung #10"] == {
        "what": "Bank documents",
        "titles_of_documents_already_filed_here": ["Gutschriftsanzeige", "Belastungsanzeige"],
    }
    assert q["criteria"]["Rechnung #12"] is None
    assert "filing conventions" in q["instructions"]
    assert "filing conventions" not in req.questions["correspondent"]["instructions"]


def test_uncertain_tags_force_review_only_when_enabled():
    c = cfg()
    meta = make_meta()
    doc = make_doc() | {"correspondent": 1, "document_type": 10}
    req = build_request(doc, meta, {}, c)
    result = interpret(jev_response(), req, meta, c)
    assert result["tags"][0]["level"] == "suggest"
    assert plan(result, doc, c)["needs_review"] is False
    assert plan(result, doc, cfg(tags_force_review=True))["needs_review"] is True
