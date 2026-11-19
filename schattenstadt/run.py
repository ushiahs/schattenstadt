"""Kommandozeile: Simulation starten und Einkaufswege vor/nach einer Schließung auswerten.

    python -m schattenstadt.run --agents 20 --ticks 100 --close supermarkt --at 50

Bei einer Schließung läuft zusätzlich ein Vergleichslauf mit gleichem Seed
ohne Schließung. Bis zur Schließung sind beide Läufe identisch. Betroffen ist
ein Agent, der ab der Schließung im Schnitt mindestens ``MINDEST_MEHRWEG``
Minuten länger zum Laden braucht als im Vergleichslauf im selben Zeitraum,
oder der bis zum Laufende nicht mehr einkauft.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .engine import Einkaufsweg, Engine, Konfig
from .policy import LLMPolicy, Policy
from .world import LAEDEN, ORTE

# Ab so vielen Minuten Mehrweg je Einkauf gilt ein Agent als betroffen. Die
# Schwelle blendet Kleinstunterschiede aus (etwa vom Park statt von der Wohnung
# aus losgegangen); größere Verschiebungen des Startorts filtert sie nicht.
MINDEST_MEHRWEG = 1.0

# Bleiben nach der Schließung weniger Ticks, warnt der Bericht: Wer dann
# nicht mehr einkauft, war oft nur noch unterwegs zum Ersatzladen.
MINDEST_RESTFENSTER = 24

GRUPPEN: tuple[tuple[str, Callable[[bool], bool]], ...] = (
    ("alle", lambda auto: True),
    ("mit Auto", lambda auto: auto),
    ("ohne Auto", lambda auto: not auto),
)


def _mittel(werte: Iterable[float]) -> float | None:
    werte = list(werte)
    return sum(werte) / len(werte) if werte else None


def _zahl(wert: float | None) -> str:
    return "-" if wert is None else f"{wert:.1f}".replace(".", ",")


def _merkmale(auto: bool, job: str | None) -> str:
    return ("mit Auto" if auto else "ohne Auto") + (", mit Job" if job else ", ohne Job")


@dataclass(frozen=True)
class Betroffenheit:
    """Mittlere Einkaufswegzeit eines Agenten ab der Schließung, mit und ohne Schließung."""

    agent_id: int
    auto: bool
    job: str | None
    mit_schliessung: float | None
    ohne_schliessung: float | None

    @property
    def vergleichbar(self) -> bool:
        return self.ohne_schliessung is not None

    @property
    def kein_einkauf_mehr(self) -> bool:
        return self.mit_schliessung is None and self.ohne_schliessung is not None

    @property
    def betroffen(self) -> bool:
        if self.ohne_schliessung is None:
            return False
        if self.mit_schliessung is None:
            return True
        return self.mit_schliessung - self.ohne_schliessung >= MINDEST_MEHRWEG

    @property
    def mehrweg(self) -> float | None:
        if self.mit_schliessung is None or self.ohne_schliessung is None:
            return None
        return self.mit_schliessung - self.ohne_schliessung


def _minuten_je_agent(wege: list[Einkaufsweg], ab: int) -> dict[int, list[int]]:
    je_agent: dict[int, list[int]] = defaultdict(list)
    for weg in wege:
        if weg.tick >= ab:
            je_agent[weg.agent_id].append(weg.minuten)
    return je_agent


def betroffenheit(lauf: Engine, vergleich: Engine) -> list[Betroffenheit]:
    ab = lauf.konfig.schliessen_bei
    if ab is None:
        raise ValueError("der Lauf hat keine Schließung")
    if [(a.id, a.auto, a.job) for a in lauf.agenten] != [(a.id, a.auto, a.job) for a in vergleich.agenten]:
        raise ValueError("Lauf und Vergleichslauf haben verschiedene Agenten")
    mit = _minuten_je_agent(lauf.einkaufswege, ab)
    ohne = _minuten_je_agent(vergleich.einkaufswege, ab)
    return [
        Betroffenheit(a.id, a.auto, a.job, _mittel(mit.get(a.id, [])), _mittel(ohne.get(a.id, [])))
        for a in lauf.agenten
    ]


def naechster_laden(lauf: Engine, von: str, auto: bool, geschlossen: Iterable[str] = ()) -> int | None:
    """Wegzeit zum nächsten Laden, der nicht in ``geschlossen`` ist."""
    zu = set(geschlossen)
    zeiten = [lauf.welt.wegzeit(von, laden, auto) for laden in LAEDEN if laden not in zu]
    return min(zeiten) if zeiten else None


def bericht(lauf: Engine, vergleich: Engine | None = None) -> str:
    k = lauf.konfig
    autos = sum(a.auto for a in lauf.agenten)
    zeilen = [
        f"Schattenstadt: {k.agenten} Agenten ({autos} mit Auto), {k.ticks} Ticks, "
        f"Seed {k.seed}, Policy {lauf.policy.name}",
    ]

    if k.schliessen is None or k.schliessen_bei is None:
        zeilen += ["Keine Schließung.", "", "Durchschnittliche Einkaufswegzeit (Minuten je Hinweg zum Laden)"]
        for name, passt in GRUPPEN:
            auswahl = [w.minuten for w in lauf.einkaufswege if passt(w.auto)]
            zeilen.append(f"  {name:<10} {_zahl(_mittel(auswahl)):>7}   ({len(auswahl)} Wege)")
    else:
        if vergleich is None:
            raise ValueError("bei einer Schließung braucht der Bericht den Vergleichslauf")
        ab = k.schliessen_bei
        zeilen.append(f"Schließung: {k.schliessen} bei Tick {ab}. Vergleich: gleicher Seed ohne Schließung.")
        rest = k.ticks - ab
        if rest < MINDEST_RESTFENSTER:
            zeilen.append(
                f"Hinweis: Nach der Schließung bleiben nur {rest} Tick{'' if rest == 1 else 's'}. "
                "„Kein Einkauf bis Laufende“ heißt dann oft nur: noch unterwegs zum Ersatzladen."
            )
        if k.schliessen in LAEDEN:
            zeilen += [
                "",
                "Weg von der Wohnung zum nächsten offenen Laden (Minuten)",
                f"  {'':<10} {'vorher':>7} {'nachher':>8}",
            ]
            for name, passt in GRUPPEN[1:]:
                gruppe = [a for a in lauf.agenten if passt(a.auto)]
                vorher = _mittel(x for a in gruppe if (x := naechster_laden(lauf, a.wohnort, a.auto)) is not None)
                nachher = _mittel(
                    x for a in gruppe if (x := naechster_laden(lauf, a.wohnort, a.auto, [k.schliessen])) is not None
                )
                zeilen.append(f"  {name:<10} {_zahl(vorher):>7} {_zahl(nachher):>8}")

        zeilen += [
            "",
            "Durchschnittliche Einkaufswegzeit (Minuten je Hinweg zum Laden)",
            f"  {'':<10} {'vorher':>7} {'nachher':>8} {'Vergleich':>10}   Wege vorher/nachher/Vergleich",
        ]
        for name, passt in GRUPPEN:
            v = [w.minuten for w in lauf.einkaufswege if passt(w.auto) and w.tick < ab]
            n = [w.minuten for w in lauf.einkaufswege if passt(w.auto) and w.tick >= ab]
            o = [w.minuten for w in vergleich.einkaufswege if passt(w.auto) and w.tick >= ab]
            zeilen.append(
                f"  {name:<10} {_zahl(_mittel(v)):>7} {_zahl(_mittel(n)):>8} {_zahl(_mittel(o)):>10}"
                f"   {len(v)}/{len(n)}/{len(o)}"
            )
        zeilen.append(f"  Vergleich: Lauf ohne Schließung, ebenfalls ab Tick {ab}.")

        alle = betroffenheit(lauf, vergleich)
        vergleichbar = [b for b in alle if b.vergleichbar]
        zeilen.append("")
        if not vergleichbar:
            zeilen.append(f"Betroffene Agenten: kein Vergleich möglich (im Vergleichslauf ab Tick {ab} kein Einkauf).")
        else:
            betroffen = [b for b in vergleichbar if b.betroffen]
            zeilen.append(
                f"Betroffene Agenten (ab Tick {ab} je Einkauf mindestens {_zahl(MINDEST_MEHRWEG)} min länger "
                f"als im Vergleich, oder bis Laufende kein Einkauf): {len(betroffen)} von {len(vergleichbar)}"
            )
            for name, passt in GRUPPEN[1:]:
                gruppe = [b for b in vergleichbar if passt(b.auto)]
                zeilen.append(f"  {name + ':':<11} {sum(b.betroffen for b in gruppe)} von {len(gruppe)}")
            ohne_einkauf = sum(b.kein_einkauf_mehr for b in vergleichbar)
            if ohne_einkauf:
                zeilen.append(f"  davon ohne Einkauf bis Laufende: {ohne_einkauf}")
            if len(vergleichbar) < len(alle):
                zeilen.append(
                    f"  ohne Vergleich (im Vergleichslauf ab Tick {ab} kein Einkauf): {len(alle) - len(vergleichbar)}"
                )

            gruppen: dict[str, list[Betroffenheit]] = defaultdict(list)
            for b in vergleichbar:
                gruppen[_merkmale(b.auto, b.job)].append(b)
            reihen = []
            for merkmale, mitglieder in gruppen.items():
                mehrweg = _mittel(b.mehrweg for b in mitglieder if b.mehrweg is not None)
                reihen.append((merkmale, mehrweg, mitglieder))
            reihen.sort(key=lambda r: (r[1] is None, -(r[1] or 0.0), r[0]))
            zeilen += ["", "Mehrweg je Einkauf gegenüber dem Vergleich, nach Gruppe"]
            for merkmale, mehrweg, mitglieder in reihen:
                wert = ("+" if mehrweg is not None and mehrweg >= 0 else "") + _zahl(mehrweg)
                anzahl = f"{len(mitglieder)} Agent" + ("" if len(mitglieder) == 1 else "en")
                zeile = f"  {merkmale:<21} {wert:>6} min   {anzahl}"
                ohne = sum(b.kein_einkauf_mehr for b in mitglieder)
                if ohne:
                    zeile += f", davon {ohne} ohne Einkauf bis Laufende"
                zeilen.append(zeile)

    z = lauf.zaehler
    routine = sum(lauf.gedaechtnis.routine().values())
    statistik = (
        f"Lauf: {z['entscheidungen']} Entscheidungen, {z['ungueltig'] + z['policy_fehler']} Fehlversuche, "
        f"{z['fallback']} Fallbacks auf Routine, {z['loop']} Loop-Eingriffe, "
        f"{lauf.gedaechtnis.anzahl_erinnerungen()} Erinnerungen gespeichert, {routine} Routineaktionen gezählt."
    )
    if z["loop_routine"]:
        statistik += f" {z['loop_routine']} Loops der RoutinePolicy (Regelfehler, Meldungen auf stderr)."
    statistik += " Invarianten in jedem Tick geprüft."
    zeilen += ["", statistik]
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
    p.add_argument(
        "--db", default=":memory:", help="SQLite-Datei für die Erinnerungen des Hauptlaufs (Standard: nur im Speicher)"
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Protokoll (Schließung, Loops) ausgeben")
    return p


def _policy(name: str) -> Policy | None:
    return LLMPolicy() if name == "llm" else None


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

    konfig = Konfig(
        agenten=args.agents,
        ticks=args.ticks,
        seed=args.seed,
        schliessen=args.close,
        schliessen_bei=args.at,
        db=args.db,
    )
    laeufe: list[Engine] = []
    try:
        lauf = Engine(konfig, policy=_policy(args.policy))
        laeufe.append(lauf)
        lauf.laufe()
        vergleich = None
        if konfig.schliessen is not None:
            ohne = dataclasses.replace(konfig, schliessen=None, schliessen_bei=None, db=":memory:")
            vergleich = Engine(ohne, policy=_policy(args.policy))
            laeufe.append(vergleich)
            vergleich.laufe()
        print(bericht(lauf, vergleich))
    finally:
        for engine in laeufe:
            engine.schliessen()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
