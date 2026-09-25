from paperless_jev.titles import build_prompt, clean_title, strip_invented_exclusions


def test_clean_title():
    assert clean_title('<think>hmm</think>\n\nTitel: "Herzpraxis Biel - Kardiologische Untersuchung"\n') == (
        "Herzpraxis Biel - Kardiologische Untersuchung"
    )
    assert clean_title("**Miele – Neue Abwaschmaschine**") == "Miele – Neue Abwaschmaschine"
    assert clean_title("x" * 300) == "x" * 128
    assert clean_title("\n  \n") == ""


def test_build_prompt_contains_facts_and_examples():
    prompt = build_prompt("A" * 5000, {"Absender": "Sunrise", "Datum": None}, ["Internetkosten März 2024"])
    assert "- Absender: Sunrise" in prompt and "- Datum:" not in prompt
    assert "- Internetkosten März 2024" in prompt
    assert "[...]" in prompt


def test_strip_invented_exclusions():
    assert strip_invented_exclusions("Invoices (Rechnung). Not: salary statements.", "rechnung, quittung") == "Invoices (Rechnung)."
    assert strip_invented_exclusions("Death, divorce. Not: none.", "Tod, nicht: Geburt") == "Death, divorce."
    assert strip_invented_exclusions("Bank statements. Not: tax certificates.", "Bankbelege. nicht: Steuer") == (
        "Bank statements. Not: tax certificates."
    )
