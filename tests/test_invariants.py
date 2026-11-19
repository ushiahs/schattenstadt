import pytest

from schattenstadt.engine import Engine, InvariantenVerletzung, Konfig
from schattenstadt.world import PARK, SUPERMARKT


@pytest.mark.parametrize("seed", [1, 42, 2026])
def test_voller_lauf_verletzt_keine_invariante(seed):
    # Die Engine prüft nach jedem Tick selbst und bricht bei einer Verletzung ab.
    engine = Engine(Konfig(seed=seed, schliessen=SUPERMARKT, schliessen_bei=50)).laufe()
    assert engine.tick == 100
    assert all(a.geld >= 0 for a in engine.agenten)
    engine.pruefe_invarianten()


def test_negatives_geld_wird_erkannt():
    engine = Engine(Konfig(agenten=3, ticks=5))
    engine.agenten[1].geld = -1
    with pytest.raises(InvariantenVerletzung, match="Geld negativ"):
        engine.pruefe_invarianten()


def test_agent_an_zwei_orten_wird_erkannt():
    engine = Engine(Konfig(agenten=3, ticks=5))
    engine.welt.platziere(0, PARK)  # zusätzlich zur Wohnung
    with pytest.raises(InvariantenVerletzung, match="an 2 Orten"):
        engine.pruefe_invarianten()


def test_agent_an_keinem_ort_wird_erkannt():
    engine = Engine(Konfig(agenten=3, ticks=5))
    engine.welt.anwesend[engine.agenten[2].ort].discard(2)
    with pytest.raises(InvariantenVerletzung, match="an 0 Orten"):
        engine.pruefe_invarianten()


def test_ort_passt_nicht_zur_belegung():
    engine = Engine(Konfig(agenten=3, ticks=5))
    engine.agenten[0].ort = PARK  # Belegung der Welt sagt weiterhin Wohnung
    with pytest.raises(InvariantenVerletzung, match="passt nicht zur Belegung"):
        engine.pruefe_invarianten()


def test_verletzung_bricht_den_tick_ab():
    engine = Engine(Konfig(agenten=3, ticks=5))
    engine.welt.platziere(1, PARK)
    with pytest.raises(InvariantenVerletzung):
        engine.schritt()
