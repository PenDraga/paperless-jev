from paperless_jev.titles import build_prompt, clean_title


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
