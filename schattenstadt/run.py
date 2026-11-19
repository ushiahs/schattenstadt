"""Kommandozeile: Simulation starten und Einkaufswege vor/nach einer Schließung auswerten.

    python -m schattenstadt.run --agents 20 --ticks 100 --close supermarkt --at 50
"""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

from .engine import Engine, Konfig
from .policy import LLMPolicy, Policy
from .world import ORTE


@dataclass(frozen=True)
class Vergleich:
    agent_id: int
    auto: bool
    job: str | None
    vorher: float
    nachher: float

    @property
    def zuwachs(self) -> float:
        return self.nachher - self.vorher


def _mittel(werte: list[int]) -> float | None:
    return sum(werte) / len(werte) if werte else None


def _zahl(wert: float | None) -> str:
    return "-" if wert is None else f"{wert:.1f}".replace(".", ",")


def vergleiche(engine: Engine) -> tuple[list[Vergleich], int]:
    """Mittlere Einkaufswegzeit je Agent vor und nach der Schließung.

    Rückgabe: Vergleiche für Agenten mit Einkaufswegen in beiden Phasen und
    die Zahl der Agenten ohne Vergleich (in einer Phase kein Einkaufsweg).
    """
    bei = engine.konfig.schliessen_bei
    assert bei is not None
    vergleiche_: list[Vergleich] = []
    ohne = 0
    for agent in engine.agenten:
        eigene = [w for w in engine.einkaufswege if w.agent_id == agent.id]
        vorher = _mittel([w.minuten for w in eigene if w.tick < bei])
        nachher = _mittel([w.minuten for w in eigene if w.tick >= bei])
        if vorher is None or nachher is None:
            ohne += 1
            continue
        vergleiche_.append(Vergleich(agent.id, agent.auto, agent.job, vorher, nachher))
    return vergleiche_, ohne


GRUPPEN: tuple[tuple[str, Callable[[bool], bool]], ...] = (
    ("alle", lambda auto: True),
    ("mit Auto", lambda auto: auto),
    ("ohne Auto", lambda auto: not auto),
)


def bericht(engine: Engine) -> str:
    k = engine.konfig
    wege = engine.einkaufswege
    autos = sum(a.auto for a in engine.agenten)
    zeilen = [
        f"Schattenstadt: {k.agenten} Agenten ({autos} mit Auto), {k.ticks} Ticks, "
        f"Seed {k.seed}, Policy {engine.policy.name}",
    ]

    if k.schliessen is None:
        zeilen += ["Keine Schließung.", "", "Durchschnittliche Einkaufswegzeit (Minuten je Hinweg)"]
        for name, passt in GRUPPEN:
            auswahl = [w.minuten for w in wege if passt(w.auto)]
            zeilen.append(f"  {name:<10} {_zahl(_mittel(auswahl)):>7}   ({len(auswahl)} Wege)")
    else:
        bei = k.schliessen_bei
        assert bei is not None
        zeilen += [
            f"Schließung: {k.schliessen} bei Tick {bei}",
            "",
            "Durchschnittliche Einkaufswegzeit (Minuten je Hinweg)",
            f"  {'':<10} {'vorher':>7} {'nachher':>8}   Wege vorher/nachher",
        ]
        for name, passt in GRUPPEN:
            v = [w.minuten for w in wege if passt(w.auto) and w.tick < bei]
            n = [w.minuten for w in wege if passt(w.auto) and w.tick >= bei]
            zeilen.append(
                f"  {name:<10} {_zahl(_mittel(v)):>7} {_zahl(_mittel(n)):>8}   {len(v)}/{len(n)}"
            )

        vergleiche_, ohne = vergleiche(engine)
        betroffen = [v for v in vergleiche_ if v.zuwachs > 0]
        zeilen += [
            "",
            f"Betroffene Agenten (nachher im Schnitt länger unterwegs): "
            f"{len(betroffen)} von {len(vergleiche_)} mit Einkäufen in beiden Phasen",
        ]
        for name, passt in GRUPPEN[1:]:
            gruppe = [v for v in vergleiche_ if passt(v.auto)]
            zeilen.append(f"  {name + ':':<11} {sum(v.zuwachs > 0 for v in gruppe)} von {len(gruppe)}")
        if ohne:
            zeilen.append(f"  ohne Vergleich (in einer Phase kein Einkauf): {ohne}")
        if betroffen:
            zeilen.append("Am stärksten betroffen:")
            for v in sorted(betroffen, key=lambda v: (-v.zuwachs, v.agent_id))[:3]:
                merkmale = ("mit Auto" if v.auto else "ohne Auto") + (", mit Job" if v.job else ", ohne Job")
                zeilen.append(
                    f"  Agent {v.agent_id:>2} ({merkmale}): {_zahl(v.vorher)} -> {_zahl(v.nachher)} min "
                    f"(+{_zahl(v.zuwachs)})"
                )

    z = engine.zaehler
    routine = sum(engine.gedaechtnis.routine().values())
    zeilen += [
        "",
        f"Lauf: {z['entscheidungen']} Entscheidungen, {z['ungueltig'] + z['policy_fehler']} Fehlversuche, "
        f"{z['fallback']} Fallbacks auf Routine, {z['loop']} Loop-Eingriffe, "
        f"{engine.gedaechtnis.anzahl_erinnerungen()} Erinnerungen gespeichert, {routine} Routineaktionen gezählt. "
        f"Invarianten in jedem Tick geprüft.",
    ]
    return "\n".join(zeilen)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m schattenstadt.run",
        description="Schattenstadt: Agenten-Simulation eines Stadtviertels (früher Prototyp).",
    )
    p.add_argument("--agents", type=int, default=20, help="Anzahl Agenten (Standard: 20)")
    p.add_argument("--ticks", type=int, default=100, help="Anzahl Ticks, ein Tick ist eine Stunde (Standard: 100)")
    p.add_argument("--close", choices=ORTE, help="Ort, der während des Laufs schließt")
    p.add_argument("--at", type=int, help="Tick, ab dem der Ort geschlossen ist")
    p.add_argument("--seed", type=int, default=42, help="Seed für alle Zufälle (Standard: 42)")
    p.add_argument(
        "--policy",
        choices=("routine", "llm"),
        default="routine",
        help="routine (Standard) oder llm (Stub ohne API-Call, fällt immer auf routine zurück)",
    )
    p.add_argument("--db", default=":memory:", help="SQLite-Datei für Erinnerungen (Standard: nur im Speicher)")
    p.add_argument("-v", "--verbose", action="store_true", help="Protokoll (Schließung, Loops) ausgeben")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.agents < 1 or args.ticks < 1:
        parser.error("--agents und --ticks müssen mindestens 1 sein")
    if (args.close is None) != (args.at is None):
        parser.error("--close und --at nur zusammen angeben")
    if args.at is not None and not 0 <= args.at < args.ticks:
        parser.error("--at muss zwischen 0 und ticks-1 liegen")
    if args.db != ":memory:" and os.path.exists(args.db):
        parser.error(f"--db {args.db} existiert schon; bitte eine neue Datei angeben")

    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR, format="%(message)s")

    policy: Policy | None = LLMPolicy() if args.policy == "llm" else None
    konfig = Konfig(
        agenten=args.agents,
        ticks=args.ticks,
        seed=args.seed,
        schliessen=args.close,
        schliessen_bei=args.at,
        db=args.db,
    )
    engine = Engine(konfig, policy=policy)
    try:
        engine.laufe()
        print(bericht(engine))
    finally:
        engine.schliessen()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
