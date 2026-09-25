from datetime import date

from paperless_jev.candidates import correspondent_candidates, find_dates


def test_find_dates_formats_and_order():
    text = (
        "Zürich, 12. März 2026\nRechnung vom 12.03.2026\n"
        "Zahlbar bis 2026-04-11. Leistungszeitraum 01.02.26 - 28.02.26.\n"
        "Geboren am 31.02.2020"  # ungültig
    )
    found = find_dates(text)
    assert [d.value for d in found] == [
        date(2026, 3, 12),
        date(2026, 4, 11),
        date(2026, 2, 1),
        date(2026, 2, 28),
    ]
    assert found[0].label == "12. März 2026"
    assert "Zürich" in found[0].context


def test_find_dates_english_and_limits():
    found = find_dates("Invoice date: March 5, 2026. Old: 01.01.1970")
    assert [d.value for d in found] == [date(2026, 3, 5)]


def test_correspondent_prefilter_only_for_many():
    few = {1: "Swisscom", 2: "Stadtwerke"}
    assert correspondent_candidates("irgendwas", few) == few

    many = {i: f"Firma {i:03d} AG" for i in range(300)}
    many[500] = "Swisscom"
    picked = correspondent_candidates("Ihre Rechnung von Swisscom (Schweiz) AG", many, preferred={7})
    assert 500 in picked
    assert 7 in picked
    assert len(picked) < 40
