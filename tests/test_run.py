import dataclasses

import pytest

from schattenstadt import run
from schattenstadt.engine import Engine, Konfig
from schattenstadt.run import MINDEST_MEHRWEG, Betroffenheit, betroffenheit, naechster_laden
from schattenstadt.world import DISCOUNTER, PARK, SUPERMARKT, WOHNUNG


def laeufe(ort, bei=50, seed=42):
    konfig = Konfig(seed=seed, schliessen=ort, schliessen_bei=bei)
    lauf = Engine(konfig).laufe()
    vergleich = Engine(dataclasses.replace(konfig, schliessen=None, schliessen_bei=None)).laufe()
    return lauf, vergleich


def test_lauf_und_vergleich_sind_bis_zur_schliessung_gleich():
    lauf, vergleich = laeufe(SUPERMARKT)
    assert [z for z in lauf.zuege if z.tick < 50] == [z for z in vergleich.zuege if z.tick < 50]


def test_ohne_unterschied_ist_niemand_betroffen():
    lauf, _ = laeufe(SUPERMARKT)
    assert not any(b.betroffen for b in betroffenheit(lauf, lauf))


def test_supermarkt_schliessung_trifft_alle_ohne_auto():
    lauf, vergleich = laeufe(SUPERMARKT)
    ohne_auto = [b for b in betroffenheit(lauf, vergleich) if b.vergleichbar and not b.auto]
    assert ohne_auto and all(b.betroffen for b in ohne_auto)


@pytest.mark.parametrize("ort", [PARK, DISCOUNTER])
def test_kleine_umwege_zaehlen_nicht_als_betroffen(ort):
    lauf, vergleich = laeufe(ort)
    assert not any(b.betroffen for b in betroffenheit(lauf, vergleich))


def test_schwelle_und_kein_einkauf_mehr():
    knapp = Betroffenheit(0, False, None, mit_schliessung=7.9, ohne_schliessung=7.0)
    deutlich = Betroffenheit(1, False, None, mit_schliessung=7.0 + MINDEST_MEHRWEG, ohne_schliessung=7.0)
    kein_einkauf = Betroffenheit(2, False, None, mit_schliessung=None, ohne_schliessung=7.0)
    ohne_vergleich = Betroffenheit(3, False, None, mit_schliessung=28.0, ohne_schliessung=None)
    assert not knapp.betroffen
    assert deutlich.betroffen
    assert kein_einkauf.betroffen and kein_einkauf.kein_einkauf_mehr
    assert not ohne_vergleich.vergleichbar and not ohne_vergleich.betroffen


def test_naechster_offener_laden_von_der_wohnung():
    lauf = Engine(Konfig(agenten=1, ticks=1))
    assert naechster_laden(lauf, WOHNUNG, auto=False) == 7
    assert naechster_laden(lauf, WOHNUNG, auto=False, geschlossen=[SUPERMARKT]) == 28
    assert naechster_laden(lauf, WOHNUNG, auto=True, geschlossen=[SUPERMARKT]) == 8
    assert naechster_laden(lauf, WOHNUNG, auto=True, geschlossen=[SUPERMARKT, DISCOUNTER]) is None


@pytest.mark.parametrize("bei", ["0", "99"])
def test_cli_randfaelle_der_schliessung(bei, capsys):
    assert run.main(["--close", SUPERMARKT, "--at", bei]) == 0
    ausgabe = capsys.readouterr().out
    assert f"Schließung: supermarkt bei Tick {bei}" in ausgabe
    assert "Betroffene Agenten" in ausgabe


def test_cli_ohne_schliessung(capsys):
    assert run.main(["--agents", "5", "--ticks", "30"]) == 0
    assert "Keine Schließung." in capsys.readouterr().out


@pytest.mark.parametrize(
    "argumente",
    [["--close", SUPERMARKT], ["--at", "10"], ["--close", SUPERMARKT, "--at", "100"], ["--agents", "0"]],
)
def test_cli_lehnt_falsche_argumente_ab(argumente):
    with pytest.raises(SystemExit) as fehler:
        run.main(argumente)
    assert fehler.value.code == 2
