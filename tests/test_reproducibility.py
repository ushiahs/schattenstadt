import os
import subprocess
import sys

from schattenstadt.engine import Engine, Konfig
from schattenstadt.world import SUPERMARKT


def fingerabdruck(seed):
    engine = Engine(Konfig(seed=seed, schliessen=SUPERMARKT, schliessen_bei=50)).laufe()
    return (
        engine.zuege,
        engine.einkaufswege,
        [(a.id, a.ort, a.hunger, a.energie, a.geld) for a in engine.agenten],
        engine.gedaechtnis._db.execute(
            "SELECT id, tick, agent_id, typ, text, wichtigkeit FROM erinnerungen ORDER BY id"
        ).fetchall(),
        engine.gedaechtnis.routine(),
        dict(engine.zaehler),
    )


def test_gleicher_seed_gleiches_ergebnis():
    assert fingerabdruck(42) == fingerabdruck(42)


def test_anderer_seed_anderes_ergebnis():
    assert fingerabdruck(42)[0] != fingerabdruck(43)[0]


def test_cli_ausgabe_unabhaengig_von_pythonhashseed():
    """Gleicher Seed muss auch in getrennten Prozessen mit anderer
    Hash-Randomisierung (Reihenfolge von String-Mengen) gleich laufen."""
    befehl = [
        sys.executable, "-m", "schattenstadt.run",
        "--agents", "20", "--ticks", "100", "--close", SUPERMARKT, "--at", "50",
    ]
    ausgaben = []
    for hashseed in ("0", "12345"):
        umgebung = {**os.environ, "PYTHONHASHSEED": hashseed}
        lauf = subprocess.run(befehl, capture_output=True, text=True, env=umgebung, check=True)
        ausgaben.append(lauf.stdout)
    assert ausgaben[0] == ausgaben[1]
    assert "Betroffene Agenten" in ausgaben[0]
