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

    # tag:103 ist schon gesetzt und wird gegengeprüft
    assert set(req.questions) == {"document_type", "correspondent", "created", "tag:101", "tag:103"}
    dt = req.questions["document_type"]
    # doppelte Namen werden eindeutig, deaktivierte fehlen, NONE ist immer dabei
    assert set(dt["criteria"]) == {"Rechnung #10", "Rechnung #12", NONE}
    assert req.options["created"]["12.03.2026"] == "2026-03-12"
    # Posteingang und Status-Tags werden nicht gefragt; gesetzte Tags nur zur Gegenprüfung
    assert "tag:100" not in req.questions and "tag:102" not in req.questions and req.checked_tags == {103}


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


def test_correspondent_fallback_when_none_fits():
    c = cfg(correspondent_fallback="stadtwerke")
    meta = make_meta()
    req = build_request(make_doc(), meta, {}, c)
    resp = jev_response()
    resp["answers"]["correspondent"] = {"type": "choice", "choice": NONE, "confidence": 0.96, "probabilities": {}}
    f = interpret(resp, req, meta, c)["fields"]["correspondent"]
    assert (f["value"], f["label"], f["level"]) == (2, "Stadtwerke", "auto")


def test_confident_conflict_with_existing_value_goes_to_review():
    c = cfg()
    meta = make_meta()
    doc = make_doc() | {"correspondent": 2, "document_type": 10}  # Jev sagt sicher: Swisscom (1)
    req = build_request(doc, meta, {}, c)
    resp = jev_response()
    resp["answers"]["correspondent"]["confidence"] = 0.97
    resp["answers"]["tag:101"]["noul"] = 0.1
    result = interpret(resp, req, meta, c)
    decision = plan(result, doc, c)
    assert "correspondent" not in decision["updates"]
    assert decision["needs_review"] is True
    assert plan(result, doc, cfg(review_conflicts=False))["needs_review"] is False


def test_paperless_rules_become_hints():
    from paperless_jev.classifier import RULE_KEY, match_words, rule_hint

    assert match_words('Police 1234567 "Bank Cler AG"') == ["Police", "1234567", "Bank Cler AG"]
    assert rule_hint({"match": "Police 1234567", "algorithm": 2}) == 'the text contains all of these words: "Police", "1234567"'
    assert rule_hint({"match": "Bank Cler AG", "algorithm": 3}) == 'the text contains exactly: "Bank Cler AG"'

    meta = make_meta()
    meta.rules = {("correspondent", 1): {"match": "swisscom.ch", "algorithm": 1, "insensitive": True},
                  ("tag", 103): {"match": "Octavia SQ7", "algorithm": 1, "insensitive": True}}
    req = build_request(make_doc(), meta, {}, cfg(paperless_rules=True))
    q = req.questions["correspondent"]
    assert q["criteria"]["Swisscom"] == {RULE_KEY: 'the text contains any of these words: "swisscom.ch"'}
    assert q["criteria"]["Stadtwerke"] is None
    assert "matching rule" in q["instructions"] and "filing conventions" not in q["instructions"]
    assert "matching rule" not in req.questions["document_type"]["instructions"]
    # Tag 103 ist schon gesetzt und wird mit Regel gegengeprüft; ohne Regel kein Kriterium
    assert "Octavia" in str(req.questions["tag:103"]["criteria"]) and "criteria" not in req.questions["tag:101"]

    # Standard: aus (Vergleich an 60 Dokumenten ohne messbaren Vorteil, +40 % Tokens)
    req = build_request(make_doc(), meta, {}, cfg())
    assert req.questions["correspondent"]["criteria"]["Swisscom"] is None


def test_existing_tags_are_verified():
    from paperless_jev.classifier import plan

    doc = make_doc()  # hat Tag 103 (Auto) und den Posteingang
    req = build_request(doc, make_meta(), {}, cfg())
    assert req.checked_tags == {103}
    assert list(req.questions).index("tag:103") < list(req.questions).index("tag:101")  # zuerst geprüft
    assert "tag:100" not in req.questions and "tag:102" not in req.questions

    response = {"answers": {"tag:103": {"noul": 0.03}, "tag:101": {"noul": 0.95}}}
    result = interpret(response, req, make_meta(), cfg())
    assert result["tags"] == [{"id": 101, "label": "Steuern", "p": 0.95, "level": "auto"}]
    assert result["tag_checks"] == [{"id": 103, "label": "Auto", "p": 0.03, "verdict": "conflict"}]
    p = plan(result, doc, cfg())
    assert p["needs_review"] and p["updates"]["tags_add"] == [101]

    # nur der Widerspruch löst das Review aus (Dokument sonst vollständig)
    complete = doc | {"document_type": 10, "correspondent": 1}
    assert plan(result, complete, cfg())["needs_review"]
    assert not plan(result, complete, cfg(review_conflicts=False))["needs_review"]
    result["tag_checks"][0].update(p=0.8, verdict="ok")
    assert not plan(result, complete, cfg())["needs_review"]

    # abschaltbar: vorhandene Tags werden dann nicht gefragt
    req = build_request(doc, make_meta(), {}, cfg(verify_tags=False))
    assert req.checked_tags == set() and "tag:103" not in req.questions


def test_confident_contradiction_overwrites_existing_value():
    from paperless_jev.classifier import plan

    doc = make_doc() | {"document_type": 10, "correspondent": 2}  # heute: Stadtwerke
    result = {"fields": {
        "document_type": {"value": 10, "confidence": 0.99, "level": "auto"},
        "correspondent": {"value": 1, "confidence": 0.99, "level": "auto"},
    }, "tags": [], "tag_checks": []}
    # Standard: nie überschreiben, Widerspruch -> Review
    p = plan(result, doc, cfg())
    assert "correspondent" not in p["updates"] and p["needs_review"]
    # ab 0.98: Jev korrigiert selbst, kein Review
    p = plan(result, doc, cfg(overwrite_above=0.98))
    assert p["updates"]["correspondent"] == 1 and not p["needs_review"]
    # knapperer Widerspruch bleibt beim Review
    result["fields"]["correspondent"]["confidence"] = 0.9
    p = plan(result, doc, cfg(overwrite_above=0.98))
    assert "correspondent" not in p["updates"] and p["needs_review"]


def test_localtime_filter():
    from paperless_jev.app import localtime

    assert localtime("2026-09-30T14:05:00+00:00") == "30.09. 16:05"  # Sommerzeit Zürich
    assert localtime("2026-12-01T14:05:00+00:00") == "01.12. 15:05"
    assert localtime(None) == "" and localtime("kaputt") == "kaputt"
