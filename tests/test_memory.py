import sqlite3
from collections import Counter

import pytest

from schattenstadt.engine import Engine, Konfig
from schattenstadt.memory import WICHTIGKEIT, Ereignis, Gedaechtnis
from schattenstadt.policy import Policy, RoutinePolicy
from schattenstadt.world import SUPERMARKT


def test_wichtigkeit_kommt_vom_ereignistyp():
    g = Gedaechtnis()
    g.merke(1, 0, Ereignis.EINKAUF, laden="Supermarkt", preis=12, weg="7 min")
    g.merke(2, 0, Ereignis.ORT_GESCHLOSSEN, ort="Supermarkt", seit=2)
    g.merke(3, 0, Ereignis.GESPRAECH, partner=4, ort="Park")
    abruf = g.abrufen(0)
    assert [e.typ for e in abruf] == [Ereignis.ORT_GESCHLOSSEN, Ereignis.GESPRAECH, Ereignis.EINKAUF]
    assert all(e.wichtigkeit == WICHTIGKEIT[e.typ] for e in abruf)
    assert abruf[0].text == "Supermarkt ist seit Tick 2 geschlossen."


def test_kein_freier_text_moeglich():
    g = Gedaechtnis()
    with pytest.raises(ValueError):
        g.merke(1, 0, "freitext_vom_modell", text="Ich habe gehört, der Discounter ist zu.")
    with pytest.raises(KeyError):
        g.merke(1, 0, Ereignis.EINKAUF)  # Vorlage verlangt Ereignisdaten


def test_routine_wird_gezaehlt_nicht_gespeichert():
    engine = Engine(Konfig(seed=3, schliessen=SUPERMARKT, schliessen_bei=50)).laufe()
    erwartet = Counter(z.action.value for z in engine.zuege if z.action.value in {"move", "work", "rest"})
    assert engine.gedaechtnis.routine() == dict(sorted(erwartet.items()))
    typen = {row[0] for row in engine.gedaechtnis._db.execute("SELECT DISTINCT typ FROM erinnerungen")}
    assert typen <= {e.value for e in Ereignis}
    assert sum(erwartet.values()) > engine.gedaechtnis.anzahl_erinnerungen()


def test_schliessung_wird_fuer_alle_agenten_erinnert():
    engine = Engine(Konfig(agenten=5, ticks=60, schliessen=SUPERMARKT, schliessen_bei=50)).laufe()
    for agent in engine.agenten:
        texte = [e.text for e in engine.gedaechtnis.abrufen(agent.id, limit=50)]
        assert "Supermarkt ist seit Tick 50 geschlossen." in texte


class MarkerPolicy(Policy):
    """Entscheidet wie die Routine, schreibt aber eine auffällige Begründung."""

    name = "marker"

    def __init__(self):
        self.routine = RoutinePolicy(seed=5)

    def entscheide(self, b, rueckmeldung=None):
        daten = self.routine.entscheide(b).model_dump(mode="json")
        daten["reason"] = "MODELLTEXT: der Discounter ist angeblich zu"
        return daten


def test_reason_landet_nie_im_gedaechtnis():
    engine = Engine(Konfig(seed=5, schliessen=SUPERMARKT, schliessen_bei=50), policy=MarkerPolicy()).laufe()
    assert {z.quelle for z in engine.zuege} == {"marker"}
    texte = [row[0] for row in engine.gedaechtnis._db.execute("SELECT text FROM erinnerungen")]
    assert texte and not any("MODELLTEXT" in t for t in texte)


def test_datenbank_als_datei(tmp_path):
    pfad = tmp_path / "erinnerungen.sqlite"
    engine = Engine(Konfig(agenten=4, ticks=30, db=str(pfad))).laufe()
    anzahl = engine.gedaechtnis.anzahl_erinnerungen()
    engine.schliessen()
    with sqlite3.connect(pfad) as db:
        assert db.execute("SELECT COUNT(*) FROM erinnerungen").fetchone()[0] == anzahl
        assert db.execute("SELECT SUM(anzahl) FROM routine").fetchone()[0] > 0
