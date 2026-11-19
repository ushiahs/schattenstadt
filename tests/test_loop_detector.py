import pytest

from schattenstadt.actions import Aktion, Beobachtung, Entscheidung
from schattenstadt.engine import Engine, Konfig, Schritt, erkenne_loop
from schattenstadt.policy import Policy
from schattenstadt.world import ORTE, PARK, SUPERMARKT, WOHNUNG

M = Aktion.MOVE


def pendel(n, wirkung=False):
    """n Schritte Wohnung -> Park -> Wohnung -> ..."""
    schritte = []
    ort = WOHNUNG
    for _ in range(n):
        ziel = PARK if ort == WOHNUNG else WOHNUNG
        schritte.append(Schritt(M, ziel, ort, wirkung))
        ort = ziel
    return schritte


def test_pendeln_dreimal_ist_ein_loop():
    assert erkenne_loop(pendel(6)) == 2


def test_zweimal_ist_noch_kein_loop():
    assert erkenne_loop(pendel(5)) is None
    assert erkenne_loop(pendel(4)) is None


def test_folge_mit_wirkung_ist_kein_loop():
    schritte = pendel(6)
    schritte[3] = Schritt(schritte[3].action, schritte[3].target, schritte[3].ort_vorher, True)
    assert erkenne_loop(schritte) is None


def test_gleiche_aktion_mit_wirkung_ist_kein_loop():
    ruhen = [Schritt(Aktion.REST, WOHNUNG, WOHNUNG, True)] * 3
    assert erkenne_loop(ruhen) is None


def test_gleiche_aktion_ohne_wirkung_ist_ein_loop():
    reden = [Schritt(Aktion.TALK, PARK, PARK, False)] * 3
    assert erkenne_loop(reden) == 1


def test_dreierfolge_wird_erkannt():
    runde = [
        Schritt(M, PARK, WOHNUNG, False),
        Schritt(M, SUPERMARKT, PARK, False),
        Schritt(M, WOHNUNG, SUPERMARKT, False),
    ]
    assert erkenne_loop(runde * 3) == 3
    assert erkenne_loop(runde * 2) is None


def test_andere_startorte_sind_kein_loop():
    # Gleiche Aktionsfolge, aber jede Wiederholung beginnt woanders.
    schritte = [
        Schritt(Aktion.TALK, PARK, WOHNUNG, False),
        Schritt(Aktion.TALK, PARK, PARK, False),
        Schritt(Aktion.TALK, PARK, SUPERMARKT, False),
    ]
    assert erkenne_loop(schritte) is None


class PendelPolicy(Policy):
    """Gültige, aber sinnlose Entscheidungen: immer zwischen Wohnung und Park hin und her."""

    name = "pendel"

    def entscheide(self, b: Beobachtung, rueckmeldung=None):
        ziel = PARK if b.ort == WOHNUNG else WOHNUNG
        return Entscheidung(agent_id=b.agent_id, tick=b.tick, action=M, target=ziel, reason="hin und her")


def test_engine_greift_bei_loop_ein_und_gibt_spaeter_zurueck():
    engine = Engine(Konfig(agenten=1, ticks=20, loop_sperre=10), policy=PendelPolicy()).laufe()

    loops = [p for p in engine.protokoll if p.art == "loop"]
    assert len(loops) == 1
    assert loops[0].tick == 5 and loops[0].agent_id == 0
    assert "move:park -> move:wohnung" in loops[0].text
    assert engine.zaehler["loop"] == 1

    quellen = {z.tick: z.quelle for z in engine.zuege}
    assert all(quellen[t] == "pendel" for t in range(0, 6))
    assert all(quellen[t] == "routine_loop" for t in range(6, 16))
    assert quellen[16] == "pendel"


@pytest.mark.parametrize("ort", [None, *ORTE])
@pytest.mark.parametrize("seed", range(5))
def test_routine_erzeugt_keine_loops(seed, ort):
    konfig = Konfig(seed=seed, schliessen=ort, schliessen_bei=None if ort is None else 50)
    engine = Engine(konfig).laufe()
    assert engine.zaehler["loop_routine"] == 0


def test_loop_der_routine_wird_als_regelfehler_gemeldet():
    """Ist die Policy schon die Routine, kann kein Eingriff helfen: Die Engine
    meldet einen Regelfehler statt eines Eingriffs."""
    engine = Engine(Konfig(agenten=1, ticks=8))
    engine.routine = engine.policy = PendelPolicy()
    engine.laufe()
    assert engine.zaehler["loop"] == 0
    assert engine.zaehler["loop_routine"] == 1
    assert [p.art for p in engine.protokoll] == ["loop_routine"]
