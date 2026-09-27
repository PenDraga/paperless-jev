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
    assert strip_invented_exclusions("Arztrechnungen, Spitalrechnungen. Nicht: Versicherungspolicen.", "Arzt, Spital") == (
        "Arztrechnungen, Spitalrechnungen."
    )
    assert strip_invented_exclusions("Kontoauszüge. Nicht: keine.", "Bank, nicht: Steuer") == "Kontoauszüge."
    assert strip_invented_exclusions("Kontoauszüge. Nicht: Steuerbescheinigungen.", "Bank, nicht: Steuer") == (
        "Kontoauszüge. Nicht: Steuerbescheinigungen."
    )


def test_looks_like_title_rejects_chat_answers():
    from paperless_jev.titles import looks_like_title

    assert looks_like_title("Stromrechnung 3. Quartal")
    assert looks_like_title("Autowerkstatt: Service Octavia")
    for bad in ["It seems like your message is repeating", "The correct title is:", "**Betreff:** November",
                "Based on the example you provided", "Rechnung Nr. 12?", "Gerne, hier der Titel", "x" * 61]:
        assert not looks_like_title(bad), bad


def test_build_prompt_skips_broken_example_titles():
    prompt = build_prompt("Text", {}, ["Internetkosten März", "Based on the example you provided, it seems"])
    assert "- Internetkosten März" in prompt and "Based on" not in prompt


async def test_generate_title_retries_once_then_gives_up(monkeypatch):
    from paperless_jev import titles
    from paperless_jev.llm import LLM, LLMError

    answers = ["Here is a title: Rechnung", "Stromrechnung 3. Quartal"]
    prompts = []

    async def fake_complete(llm, prompt, max_tokens):
        prompts.append(prompt)
        return answers.pop(0)

    monkeypatch.setattr(titles, "complete", fake_complete)
    llm = LLM("ollama", "http://ollama:11434", "qwen3:8b")
    assert await titles.generate_title(llm, "Text", {}, []) == "Stromrechnung 3. Quartal"
    assert "ausschliesslich mit dem Titel" in prompts[1]

    answers[:] = ["Could you clarify?", "Sure, here is it"]
    import pytest
    with pytest.raises(LLMError):
        await titles.generate_title(llm, "Text", {}, [])


async def test_generate_title_asks_for_shorter_and_accepts_long_title(monkeypatch):
    from paperless_jev import titles
    from paperless_jev.llm import LLM

    long_title = "Bauhaus - Teelicht, Servietten, Frosch Nachfüllung, Schubladenboxen und Papiere"
    llm = LLM("ollama", "http://ollama:11434", "qwen3:8b")
    prompts = []

    async def fake(answers):
        async def complete(llm, prompt, max_tokens):
            prompts.append(prompt)
            return answers.pop(0)
        monkeypatch.setattr(titles, "complete", complete)

    await fake([long_title, "Bauhaus - Haushaltsartikel"])
    assert await titles.generate_title(llm, "Text", {}, []) == "Bauhaus - Haushaltsartikel"
    assert "zu lang" in prompts[-1] and long_title in prompts[-1]

    # bleibt es zu lang, wird der kürzere von beiden genommen statt gar keiner
    await fake([long_title, long_title + " Einkauf"])
    assert await titles.generate_title(llm, "Text", {}, []) == long_title
