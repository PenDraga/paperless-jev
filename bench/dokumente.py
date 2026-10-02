"""Erfundene Testdokumente mit bekannter richtiger Antwort.

Alle Namen, Adressen und Nummern sind frei erfunden. Die Fälle sind absichtlich
gemischt: eindeutige Belege, ähnliche Dokumenttypen, mehrere Daten im Text,
Empfänger, die wie Absender aussehen, und Dokumente ohne passenden Korrespondenten.
"""

from __future__ import annotations

DOCUMENT_TYPES = {
    "Rechnung": "Zahlungsaufforderung für eine Lieferung oder Leistung – nicht: Mahnung, Gutschrift, Quittung",
    "Mahnung": "Zahlungserinnerung oder Mahnung zu einer bereits gestellten Rechnung",
    "Gutschrift": "Rückerstattung oder Gutschrift zugunsten des Empfängers",
    "Quittung": "Kassenbon oder Zahlungsbestätigung, bereits bezahlt",
    "Kontoauszug": "Bankauszug mit Kontobewegungen und Saldo – nicht: Steuerbescheinigung",
    "Steuerbescheinigung": "Jahres- oder Zinsbescheinigung für die Steuererklärung, Säule-3a-Bescheinigung",
    "Lohnausweis": "Jährlicher Lohnausweis des Arbeitgebers",
    "Lohnabrechnung": "Monatliche Lohnabrechnung",
    "Vertrag": "Vertrag oder Vereinbarung mit Unterschriften",
    "Police": "Versicherungspolice oder Policennachtrag",
    "Offerte": "Angebot oder Kostenvoranschlag vor einer Bestellung",
    "Arztbericht": "Befund, Bericht oder Zeugnis eines Arztes oder Spitals",
    "Behördenschreiben": "Brief einer Gemeinde, eines Kantons oder Bundesamts – nicht: Steuerrechnung",
    "Kündigung": "Kündigung eines Vertrags, Abos oder einer Wohnung",
    "Lieferschein": "Lieferschein ohne Zahlungsaufforderung",
    "Garantieschein": "Garantie- oder Serviceschein zu einem Gerät",
}

CORRESPONDENTS = [
    "Elektra Seewald AG", "Aarvita Krankenkasse", "Ruvia Versicherungen", "Bank Linthal", "Gemeinde Wiesental",
    "Steuerverwaltung Kanton Belmont", "Garage Rotfluh GmbH", "Praxis Dr. Imfeld", "Spital Seeblick",
    "Telnova AG", "Haushalt Plus AG", "Möbel Horn AG", "Bauunternehmung Gerber & Co.", "Sanitär Kälin",
    "Pensionskasse Meridian", "Kessler Logistik AG", "Vorsorgestiftung Alpina 3a", "Finova Bank",
    "Strassenverkehrsamt Belmont", "Hausverwaltung Sonnhalde", "Zahnarztpraxis Lindenhof", "Optik Weitblick",
    "Velo Brändli", "Reisebüro Fernweh", "Kita Sonnenkäfer", "Musikschule Allegro", "Heizöl Frey AG",
    "Kaminfeger Russ", "Gartenbau Grünzeit", "Elektro Funke AG", "Druckerei Lettra", "Online-Shop Gadgetwelt",
    "Tierarztpraxis Pfote", "Fitnesspark Vita", "Rechtsanwalt lic. iur. Bärtschi", "Notariat Hofmatt",
]

TAGS = {
    "Steuerrelevant": "Belege für die Steuererklärung: Lohnausweis, Zins- und 3a-Bescheinigungen, Spenden, Krankheitskosten über Franchise",
    "Haus": "Eigenheim: Energie, Unterhalt, Handwerker, Gebäudeversicherung, Hypothek",
    "Auto": "Fahrzeug: Service, Reparatur, Verkehrsamt, Autoversicherung",
    "Gesundheit": "Arzt, Spital, Zahnarzt, Krankenkasse, Optiker",
    "Versicherung": "Policen und Prämien aller Versicherungen inkl. Krankenkasse",
    "Kinder": "Kita, Schule, Musikschule, Kinderarzt",
    "Arbeit": "Arbeitgeber, Lohn, Pensionskasse",
    "Garantie": "Kaufbelege und Garantiescheine von Geräten, die man für die Garantie aufbewahrt",
    "Bank": "Kontoauszüge, Zinsen, Bankkorrespondenz",
    "Ferien": "Reisen, Hotels, Flüge",
}

# Felder: text, document_type, correspondent (None = keiner passt), created (ISO), tags
DOCUMENTS: list[dict] = [
    {"text": """Elektra Seewald AG · Industriestrasse 4 · 3270 Aarberg
Familie Muster, Dorfweg 4, 3270 Aarberg
Rechnung Nr. 2026-58812                Aarberg, 14.03.2026
Stromlieferung 01.10.2025 – 31.12.2025, Zähler 77812
Energie Hochtarif 1'240 kWh  CHF 298.60
Netznutzung CHF 141.20  Abgaben CHF 22.40
Total CHF 462.20, zahlbar bis 13.04.2026""",
     "document_type": "Rechnung", "correspondent": "Elektra Seewald AG", "created": "2026-03-14", "tags": ["Haus"]},
    {"text": """Elektra Seewald AG
1. Mahnung                                   Aarberg, 22.04.2026
Unsere Rechnung Nr. 2026-58812 vom 14.03.2026 über CHF 462.20 ist noch offen.
Bitte begleichen Sie den Betrag bis 05.05.2026. Mahngebühr CHF 20.00.""",
     "document_type": "Mahnung", "correspondent": "Elektra Seewald AG", "created": "2026-04-22", "tags": ["Haus"]},
    {"text": """Aarvita Krankenkasse – Leistungsabrechnung
Datum: 03.02.2026   Versicherte Person: Lena Muster, geb. 12.07.2019
Behandlung Praxis Dr. Imfeld 15.01.2026, Rechnungsbetrag CHF 186.40
Franchise CHF 0.00, Selbstbehalt CHF 18.65
Wir überweisen Ihnen CHF 167.75 auf Ihr Konto (Gutschrift).""",
     "document_type": "Gutschrift", "correspondent": "Aarvita Krankenkasse", "created": "2026-02-03", "tags": ["Gesundheit", "Kinder", "Versicherung"]},
    {"text": """Aarvita Krankenkasse
Versicherungspolice 2027                    Bern, 28.09.2026
Versicherte: Peter Muster, geb. 03.05.1984
Grundversicherung KVG, Franchise CHF 2'500, Modell Hausarzt
Zusatzversicherung Spital halbprivat
Monatsprämie ab 01.01.2027: CHF 412.30""",
     "document_type": "Police", "correspondent": "Aarvita Krankenkasse", "created": "2026-09-28", "tags": ["Versicherung", "Gesundheit"]},
    {"text": """Ruvia Versicherungen
Motorfahrzeugversicherung – Policennachtrag       Lausanne, 11.06.2026
Fahrzeug: VW Golf, Kontrollschild BE 123 456
Neuer Bonusstufe 40 % ab 01.07.2026. Jahresprämie CHF 784.00""",
     "document_type": "Police", "correspondent": "Ruvia Versicherungen", "created": "2026-06-11", "tags": ["Versicherung", "Auto"]},
    {"text": """Bank Linthal
Kontoauszug Privatkonto CH12 0000 1111 2222 3   Periode 01.08.2026 – 31.08.2026
Saldo Vortrag 4'210.35
02.08. Lohn Kessler Logistik AG  +6'480.00
05.08. Dauerauftrag Hausverwaltung Sonnhalde  -1'950.00
19.08. Karte Gadgetwelt  -249.00
Saldo per 31.08.2026: 8'491.35""",
     "document_type": "Kontoauszug", "correspondent": "Bank Linthal", "created": "2026-08-31", "tags": ["Bank"]},
    {"text": """Bank Linthal
Zins- und Kapitalbescheinigung 2025 für die Steuererklärung
Erstellt am 15.01.2026
Sparkonto CH98 0000 3333 4444 5: Saldo per 31.12.2025 CHF 18'422.10, Zinsertrag CHF 36.80
Verrechnungssteuer 35 %: CHF 0.00 (unter Freigrenze)""",
     "document_type": "Steuerbescheinigung", "correspondent": "Bank Linthal", "created": "2026-01-15", "tags": ["Steuerrelevant", "Bank"]},
    {"text": """Vorsorgestiftung Alpina 3a
Bescheinigung über Vorsorgebeiträge 2025 (Säule 3a)
Datum 10.01.2026
Vorsorgenehmer: Peter Muster
Einbezahlte Beiträge 2025: CHF 7'258.00 – abzugsfähig gemäss Art. 82 BVG""",
     "document_type": "Steuerbescheinigung", "correspondent": "Vorsorgestiftung Alpina 3a", "created": "2026-01-10", "tags": ["Steuerrelevant"]},
    {"text": """Kessler Logistik AG, Personalabteilung
Lohnausweis 2025 (Formular 11)               ausgestellt am 20.01.2026
Arbeitnehmer: Peter Muster, AHV-Nr. 756.0000.0000.00
Bruttolohn CHF 84'240.–, Beiträge AHV/IV/EO/ALV CHF 5'349.–, BVG CHF 4'980.–
Nettolohn CHF 73'911.–""",
     "document_type": "Lohnausweis", "correspondent": "Kessler Logistik AG", "created": "2026-01-20", "tags": ["Steuerrelevant", "Arbeit"]},
    {"text": """Kessler Logistik AG
Lohnabrechnung September 2026                 Auszahlung 25.09.2026
Grundlohn 6'480.00, Kinderzulage 230.00
AHV/IV/EO -356.40, ALV -71.30, BVG -415.00
Auszahlung CHF 5'867.30""",
     "document_type": "Lohnabrechnung", "correspondent": "Kessler Logistik AG", "created": "2026-09-25", "tags": ["Arbeit"]},
    {"text": """Garage Rotfluh GmbH – Ihre Garage im Seeland
Rechnung 4471                                 Lyss, 07.05.2026
Fahrzeug VW Golf BE 123 456, km 84'210
Grosser Service inkl. Bremsflüssigkeit CHF 612.00
Pneuwechsel Sommer CHF 80.00
Total CHF 692.00, zahlbar innert 30 Tagen""",
     "document_type": "Rechnung", "correspondent": "Garage Rotfluh GmbH", "created": "2026-05-07", "tags": ["Auto"]},
    {"text": """Garage Rotfluh GmbH
Offerte Nr. O-2290                            Lyss, 18.10.2026
Ersatz Kupplung VW Golf, Arbeit und Material
Gesamtbetrag CHF 1'480.00, gültig bis 30.11.2026""",
     "document_type": "Offerte", "correspondent": "Garage Rotfluh GmbH", "created": "2026-10-18", "tags": ["Auto"]},
    {"text": """Strassenverkehrsamt des Kantons Belmont
Aufgebot zur periodischen Fahrzeugprüfung     Belmont, 02.03.2026
Fahrzeug VW Golf, Kontrollschild BE 123 456, erste Inverkehrsetzung 14.06.2018
Bitte melden Sie das Fahrzeug bis 31.05.2026 zur Prüfung an.""",
     "document_type": "Behördenschreiben", "correspondent": "Strassenverkehrsamt Belmont", "created": "2026-03-02", "tags": ["Auto"]},
    {"text": """Praxis Dr. med. A. Imfeld, Kinder- und Jugendmedizin
Ärztlicher Bericht                            Wiesental, 16.01.2026
Patientin: Lena Muster, geb. 12.07.2019
Untersuchung vom 15.01.2026: akute Otitis media links. Therapie: Amoxicillin 7 Tage.
Kontrolle in 10 Tagen.""",
     "document_type": "Arztbericht", "correspondent": "Praxis Dr. Imfeld", "created": "2026-01-16", "tags": ["Gesundheit", "Kinder"]},
    {"text": """Spital Seeblick, Orthopädie
Austrittsbericht                              Datum: 09.04.2026
Patient: Peter Muster, geb. 03.05.1984
Eintritt 06.04.2026, Austritt 09.04.2026. Arthroskopie Knie rechts, komplikationsloser Verlauf.
Arbeitsunfähigkeit 100 % bis 23.04.2026.""",
     "document_type": "Arztbericht", "correspondent": "Spital Seeblick", "created": "2026-04-09", "tags": ["Gesundheit"]},
    {"text": """Zahnarztpraxis Lindenhof
Rechnung                                      27.08.2026
Patient: Peter Muster
Dentalhygiene 60 Min. CHF 165.00, Röntgen CHF 42.00
Total CHF 207.00 – zahlbar bis 26.09.2026""",
     "document_type": "Rechnung", "correspondent": "Zahnarztpraxis Lindenhof", "created": "2026-08-27", "tags": ["Gesundheit"]},
    {"text": """Optik Weitblick
Quittung / Kassenbeleg   12.06.2026 16:42
Gleitsichtgläser, Fassung Modell Linea  CHF 890.00
Bezahlt mit Karte. Garantie 2 Jahre auf Fassung.""",
     "document_type": "Quittung", "correspondent": "Optik Weitblick", "created": "2026-06-12", "tags": ["Gesundheit", "Garantie"]},
    {"text": """Telnova AG
Ihre Rechnung Mobile & Internet               Rechnungsdatum 05.09.2026
Kundennummer 8831-22
Abo Telnova Home 1 Gbit/s  CHF 59.00
Abo Mobile Unlimited  CHF 45.00
Total CHF 104.00, fällig am 05.10.2026""",
     "document_type": "Rechnung", "correspondent": "Telnova AG", "created": "2026-09-05", "tags": []},
    {"text": """An: Telnova AG, Kundendienst, Postfach, 8000 Zürich
Peter Muster, Dorfweg 4, 3270 Aarberg       Aarberg, 30.06.2026
Kündigung Abo Mobile Unlimited, Kundennummer 8831-22
Hiermit kündige ich mein Abo fristgerecht per 30.09.2026.
Freundliche Grüsse, Peter Muster""",
     "document_type": "Kündigung", "correspondent": "Telnova AG", "created": "2026-06-30", "tags": []},
    {"text": """Online-Shop Gadgetwelt
Rechnung / Lieferschein Nr. 7712004            Bestelldatum 17.08.2026
Lieferung an: Peter Muster, Dorfweg 4, 3270 Aarberg
1× Kaffeevollautomat Barista 3000   CHF 249.00
Bereits bezahlt per Kreditkarte. Garantie: 24 Monate ab Kaufdatum, bitte Beleg aufbewahren.
Versanddatum 19.08.2026""",
     "document_type": "Quittung", "correspondent": "Online-Shop Gadgetwelt", "created": "2026-08-17", "tags": ["Garantie"]},
    {"text": """Haushalt Plus AG
Garantiezertifikat                            Kaufdatum: 03.03.2026
Gerät: Waschmaschine Aqua 8 kg, Seriennummer WP-883120
Garantiedauer: 3 Jahre Vollgarantie inkl. Arbeit und Anfahrt""",
     "document_type": "Garantieschein", "correspondent": "Haushalt Plus AG", "created": "2026-03-03", "tags": ["Garantie", "Haus"]},
    {"text": """Möbel Horn AG
Lieferschein 55210                            Lieferdatum 21.11.2026
Ecksofa Modell Tessa, Stoff grau, 1 Stück
Lieferung und Montage durch Möbel Horn Logistik. Rechnung folgt separat.""",
     "document_type": "Lieferschein", "correspondent": "Möbel Horn AG", "created": "2026-11-21", "tags": []},
    {"text": """Bauunternehmung Gerber & Co.
Werkvertrag                                   Aarberg, 14.02.2026
zwischen Familie Muster (Bauherrschaft) und Gerber & Co. (Unternehmer)
Gegenstand: Anbau Wintergarten, Werkpreis pauschal CHF 48'500.–
Baubeginn 01.04.2026, Fertigstellung 30.06.2026
Unterschriften: Bauherrschaft / Unternehmer""",
     "document_type": "Vertrag", "correspondent": "Bauunternehmung Gerber & Co.", "created": "2026-02-14", "tags": ["Haus"]},
    {"text": """Sanitär Kälin
Rechnung Nr. 2026-311                         08.07.2026
Reparatur Boiler, Ersatz Thermostat, Arbeit 2.5 h
Material CHF 186.00, Arbeit CHF 287.50, Total inkl. MwSt CHF 509.85""",
     "document_type": "Rechnung", "correspondent": "Sanitär Kälin", "created": "2026-07-08", "tags": ["Haus"]},
    {"text": """Gemeinde Wiesental, Gemeindeverwaltung
Verfügung Baubewilligung                      Wiesental, 12.03.2026
Gesuchsteller: Familie Muster, Parzelle 1182
Die Baubewilligung für den Anbau eines Wintergartens wird erteilt.
Rechtsmittelbelehrung: Beschwerde innert 30 Tagen.""",
     "document_type": "Behördenschreiben", "correspondent": "Gemeinde Wiesental", "created": "2026-03-12", "tags": ["Haus"]},
    {"text": """Steuerverwaltung des Kantons Belmont
Definitive Veranlagungsverfügung Steuerjahr 2025      Belmont, 19.10.2026
Steuerbares Einkommen CHF 71'300, Steuerbares Vermögen CHF 142'000
Kantons- und Gemeindesteuer CHF 9'812.40, bereits bezahlt CHF 9'500.00""",
     "document_type": "Behördenschreiben", "correspondent": "Steuerverwaltung Kanton Belmont", "created": "2026-10-19", "tags": ["Steuerrelevant"]},
    {"text": """Kita Sonnenkäfer
Rechnung Betreuung Oktober 2026               Datum 01.10.2026
Kind: Lena Muster, 2 Tage pro Woche
Betreuungstarif CHF 1'040.00, Verpflegung CHF 96.00
Total CHF 1'136.00, zahlbar bis 31.10.2026""",
     "document_type": "Rechnung", "correspondent": "Kita Sonnenkäfer", "created": "2026-10-01", "tags": ["Kinder", "Steuerrelevant"]},
    {"text": """Musikschule Allegro
Anmeldebestätigung und Vereinbarung Schuljahr 2026/27     15.06.2026
Schülerin: Lena Muster, Instrument Violine, 30 Min. Einzelunterricht
Semesterbeitrag CHF 620.– (Rechnung folgt im August)
Unterschrift Erziehungsberechtigte""",
     "document_type": "Vertrag", "correspondent": "Musikschule Allegro", "created": "2026-06-15", "tags": ["Kinder"]},
    {"text": """Reisebüro Fernweh
Buchungsbestätigung / Rechnung R-99812        Datum 02.05.2026
Pauschalreise Kreta, 12.08.2026 – 26.08.2026, 2 Erwachsene, 1 Kind
Hotel Akti Beach, Halbpension, Flug ab Zürich
Gesamtpreis CHF 4'860.00, Anzahlung CHF 1'000.00 bis 12.05.2026""",
     "document_type": "Rechnung", "correspondent": "Reisebüro Fernweh", "created": "2026-05-02", "tags": ["Ferien"]},
    {"text": """Hotel Bergkristall, Arosa
Rechnung Zimmer 214                           Abreise 16.02.2026
Gäste: Familie Muster, Aufenthalt 13.02.2026 – 16.02.2026
3 Nächte Doppelzimmer + Kinderbett CHF 870.00, Kurtaxe CHF 27.00
Bezahlt mit Karte am 16.02.2026""",
     "document_type": "Quittung", "correspondent": None, "created": "2026-02-16", "tags": ["Ferien"]},
    {"text": """Hausverwaltung Sonnhalde
Nebenkostenabrechnung 01.07.2025 – 30.06.2026      Erstellt am 15.09.2026
Mietobjekt: 4.5-Zimmer-Wohnung, Seestrasse 9
Heizung und Warmwasser CHF 1'420.80, Hauswart CHF 312.00
Akontozahlungen CHF 1'800.00, Nachzahlung CHF 0.00, Guthaben CHF 67.20 – wird gutgeschrieben""",
     "document_type": "Gutschrift", "correspondent": "Hausverwaltung Sonnhalde", "created": "2026-09-15", "tags": []},
    {"text": """Verein Tierschutz Seeland
Spendenbestätigung 2025                       Datum: 31.01.2026
Wir bestätigen den Eingang Ihrer Spenden im Jahr 2025 von total CHF 300.00.
Der Verein ist steuerbefreit; Spenden sind abzugsfähig.""",
     "document_type": "Steuerbescheinigung", "correspondent": None, "created": "2026-01-31", "tags": ["Steuerrelevant"]},
]
