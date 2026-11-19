import json

import pytest

from schattenstadt.actions import (
    Aktion,
    Beobachtung,
    Entscheidung,
    UngueltigeEntscheidung,
    gueltige_optionen,
    pruefe,
)
from schattenstadt.agent import Agent
from schattenstadt.memory import Ereignis, Erinnerung
from schattenstadt.world import ARBEIT, DISCOUNTER, PARK, SUPERMARKT, WOHNUNG, Welt


def agent(ort=WOHNUNG, geld=50, job=ARBEIT, auto=False, agent_id=0):
    return Agent(id=agent_id, wohnort=WOHNUNG, job=job, auto=auto, ort=ort, hunger=50, energie=50, geld=geld)


def beobachtung(welt, a, erinnerungen=()):
    return Beobachtung(
        tick=7,
        stunde=13,
        agent_id=a.id,
        ort=a.ort,
        hunger=a.hunger,
        energie=a.energie,
        geld=a.geld,
        job=a.job,
        auto=a.auto,
        optionen=gueltige_optionen(welt, a),
        erinnerungen=tuple(erinnerungen),
        geschlossen=welt.geschlossene_orte(),
    )


def roh(action, target, **extra):
    daten = {"agent_id": 0, "tick": 7, "action": action, "target": target, "reason": "Test"}
    daten.update(extra)
    return daten


@pytest.fixture
def welt():
    w = Welt()
    w.platziere(0, WOHNUNG)
    return w


def test_gueltiger_move_wird_angenommen(welt):
    b = beobachtung(welt, agent())
    e = pruefe(roh("move", SUPERMARKT), b)
    assert e.action is Aktion.MOVE and e.target == SUPERMARKT


def test_json_text_wird_angenommen(welt):
    b = beobachtung(welt, agent())
    e = pruefe(json.dumps(roh("rest", WOHNUNG)), b)
    assert e.action is Aktion.REST


def test_geschlossenes_ziel_ist_ungueltig(welt):
    welt.schliesse(SUPERMARKT)
    b = beobachtung(welt, agent())
    with pytest.raises(UngueltigeEntscheidung, match="geschlossen"):
        pruefe(roh("move", SUPERMARKT), b)


def test_geschlossene_orte_tauchen_nicht_in_optionen_auf(welt):
    welt.schliesse(SUPERMARKT)
    optionen = gueltige_optionen(welt, agent())
    assert all(o.target != SUPERMARKT for o in optionen)
    assert any(o.target == DISCOUNTER for o in optionen)


def test_einkauf_im_geschlossenen_laden_ist_ungueltig(welt):
    welt.bewege(0, WOHNUNG, SUPERMARKT)
    a = agent(ort=SUPERMARKT)
    welt.schliesse(SUPERMARKT)
    with pytest.raises(UngueltigeEntscheidung):
        pruefe(roh("buy", SUPERMARKT), beobachtung(welt, a))


def test_unbekannter_ort_ist_ungueltig(welt):
    with pytest.raises(UngueltigeEntscheidung, match="unbekannter Ort"):
        pruefe(roh("move", "rathaus"), beobachtung(welt, agent()))


def test_move_zum_eigenen_ort_ist_ungueltig(welt):
    with pytest.raises(UngueltigeEntscheidung):
        pruefe(roh("move", WOHNUNG), beobachtung(welt, agent()))


def test_unbekannte_aktion_ist_ungueltig(welt):
    with pytest.raises(UngueltigeEntscheidung, match="action"):
        pruefe(roh("fly", PARK), beobachtung(welt, agent()))


def test_einkauf_ohne_geld_ist_ungueltig(welt):
    welt.bewege(0, WOHNUNG, SUPERMARKT)
    a = agent(ort=SUPERMARKT, geld=5)
    with pytest.raises(UngueltigeEntscheidung):
        pruefe(roh("buy", SUPERMARKT), beobachtung(welt, a))


def test_einkauf_nur_am_aktuellen_laden(welt):
    with pytest.raises(UngueltigeEntscheidung):
        pruefe(roh("buy", SUPERMARKT), beobachtung(welt, agent()))


def test_arbeit_nur_mit_job_am_arbeitsort(welt):
    welt.bewege(0, WOHNUNG, ARBEIT)
    mit_job = beobachtung(welt, agent(ort=ARBEIT))
    assert pruefe(roh("work", ARBEIT), mit_job).action is Aktion.WORK
    ohne_job = beobachtung(welt, agent(ort=ARBEIT, job=None))
    with pytest.raises(UngueltigeEntscheidung):
        pruefe(roh("work", ARBEIT), ohne_job)


def test_gespraech_braucht_jemanden_am_ort(welt):
    allein = beobachtung(welt, agent())
    with pytest.raises(UngueltigeEntscheidung):
        pruefe(roh("talk", WOHNUNG), allein)
    welt.platziere(1, WOHNUNG)
    zu_zweit = beobachtung(welt, agent())
    assert pruefe(roh("talk", WOHNUNG), zu_zweit).action is Aktion.TALK


def test_falscher_agent_oder_tick_ist_ungueltig(welt):
    b = beobachtung(welt, agent())
    with pytest.raises(UngueltigeEntscheidung, match="agent_id"):
        pruefe(roh("rest", WOHNUNG, agent_id=3), b)
    with pytest.raises(UngueltigeEntscheidung, match="tick"):
        pruefe(roh("rest", WOHNUNG, tick=8), b)


def test_memory_ids_muessen_gezeigt_worden_sein(welt):
    erinnerung = Erinnerung(id=11, tick=3, agent_id=0, typ=Ereignis.EINKAUF, text="x", wichtigkeit=3)
    b = beobachtung(welt, agent(), erinnerungen=[erinnerung])
    assert pruefe(roh("rest", WOHNUNG, memory_ids=[11]), b).memory_ids == [11]
    with pytest.raises(UngueltigeEntscheidung, match="memory_ids"):
        pruefe(roh("rest", WOHNUNG, memory_ids=[12]), b)


def test_zusaetzliche_felder_sind_ungueltig(welt):
    with pytest.raises(UngueltigeEntscheidung):
        pruefe(roh("rest", WOHNUNG, hunger=0), beobachtung(welt, agent()))


def test_leere_begruendung_ist_ungueltig(welt):
    with pytest.raises(UngueltigeEntscheidung, match="reason"):
        pruefe(roh("rest", WOHNUNG, reason=""), beobachtung(welt, agent()))


def test_fertiges_objekt_wird_neu_geprueft(welt):
    """Auch ein bereits gebautes Entscheidung-Objekt muss zur Lage passen."""
    b = beobachtung(welt, agent())
    objekt = Entscheidung(agent_id=0, tick=7, action=Aktion.MOVE, target=SUPERMARKT, reason="Test")
    welt.schliesse(SUPERMARKT)
    b_nach = beobachtung(welt, agent())
    assert pruefe(objekt, b).target == SUPERMARKT
    with pytest.raises(UngueltigeEntscheidung):
        pruefe(objekt, b_nach)


def test_wegzeit_haengt_vom_auto_ab(welt):
    zu_fuss = {o.target: o.minuten for o in gueltige_optionen(welt, agent(auto=False))}
    mit_auto = {o.target: o.minuten for o in gueltige_optionen(welt, agent(auto=True))}
    assert mit_auto[DISCOUNTER] < zu_fuss[DISCOUNTER]
    assert zu_fuss[SUPERMARKT] < zu_fuss[DISCOUNTER]
