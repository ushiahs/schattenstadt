"""Gedächtnis der Agenten in SQLite.

Erinnerungen entstehen nur aus echten Ereignissen der Engine. ``merke()``
nimmt keinen freien Text an, sondern einen Ereignistyp und die Daten des
Ereignisses; der Text wird aus einer festen Vorlage gebaut. So kann kein
Modelltext (etwa die ``reason`` einer Entscheidung) ins Gedächtnis gelangen.

Die Wichtigkeit (1-10) hängt allein vom Ereignistyp ab. Routine (Wege,
Arbeit, Ausruhen) wird je Agent und Aktion nur gezählt, nicht einzeln
gespeichert.
"""

from __future__ import annotations

import sqlite3
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Ereignis(StrEnum):
    ORT_GESCHLOSSEN = "ort_geschlossen"
    HUNGER_KRITISCH = "hunger_kritisch"
    LADEN_GEWECHSELT = "laden_gewechselt"
    GESPRAECH = "gespraech"
    EINKAUF = "einkauf"


WICHTIGKEIT: dict[Ereignis, int] = {
    Ereignis.ORT_GESCHLOSSEN: 9,
    Ereignis.HUNGER_KRITISCH: 8,
    Ereignis.LADEN_GEWECHSELT: 6,
    Ereignis.GESPRAECH: 4,
    Ereignis.EINKAUF: 3,
}

VORLAGEN: dict[Ereignis, str] = {
    Ereignis.ORT_GESCHLOSSEN: "{ort} ist seit Tick {seit} geschlossen.",
    Ereignis.HUNGER_KRITISCH: "Hunger kritisch ({hunger}/100), Ort: {ort}.",
    Ereignis.LADEN_GEWECHSELT: "Eingekauft im {laden} statt wie zuletzt im {vorher}: {preis} €, Weg: {weg}.",
    Ereignis.GESPRAECH: "Gespräch mit Agent {partner}, Ort: {ort}.",
    Ereignis.EINKAUF: "Eingekauft im {laden}: {preis} €, Weg: {weg}.",
}

ROUTINE_AKTIONEN: frozenset[str] = frozenset({"move", "work", "rest"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS erinnerungen (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tick        INTEGER NOT NULL,
    agent_id    INTEGER NOT NULL,
    typ         TEXT    NOT NULL,
    text        TEXT    NOT NULL,
    wichtigkeit INTEGER NOT NULL CHECK (wichtigkeit BETWEEN 1 AND 10)
);
CREATE INDEX IF NOT EXISTS erinnerungen_abruf
    ON erinnerungen (agent_id, wichtigkeit DESC, tick DESC);
CREATE TABLE IF NOT EXISTS routine (
    agent_id INTEGER NOT NULL,
    aktion   TEXT    NOT NULL,
    anzahl   INTEGER NOT NULL,
    PRIMARY KEY (agent_id, aktion)
);
"""


class Erinnerung(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    tick: int
    agent_id: int
    typ: Ereignis
    text: str
    wichtigkeit: int


class Gedaechtnis:
    def __init__(self, pfad: str = ":memory:") -> None:
        self._db = sqlite3.connect(pfad)
        self._db.executescript(_SCHEMA)

    def merke(self, tick: int, agent_id: int, typ: Ereignis, **daten: object) -> int:
        """Speichert ein Ereignis und gibt die ID der Erinnerung zurück."""
        typ = Ereignis(typ)
        text = VORLAGEN[typ].format(**daten)
        cursor = self._db.execute(
            "INSERT INTO erinnerungen (tick, agent_id, typ, text, wichtigkeit) VALUES (?, ?, ?, ?, ?)",
            (tick, agent_id, typ.value, text, WICHTIGKEIT[typ]),
        )
        return int(cursor.lastrowid)

    def zaehle_routine(self, agent_id: int, aktion: str) -> None:
        if aktion not in ROUTINE_AKTIONEN:
            raise ValueError(f"keine Routineaktion: {aktion}")
        self._db.execute(
            "INSERT INTO routine (agent_id, aktion, anzahl) VALUES (?, ?, 1) "
            "ON CONFLICT (agent_id, aktion) DO UPDATE SET anzahl = anzahl + 1",
            (agent_id, aktion),
        )

    def abrufen(self, agent_id: int, limit: int = 5) -> tuple[Erinnerung, ...]:
        """Die wichtigsten Erinnerungen eines Agenten, bei Gleichstand die neuesten."""
        zeilen = self._db.execute(
            "SELECT id, tick, agent_id, typ, text, wichtigkeit FROM erinnerungen "
            "WHERE agent_id = ? ORDER BY wichtigkeit DESC, tick DESC, id DESC LIMIT ?",
            (agent_id, limit),
        ).fetchall()
        return tuple(
            Erinnerung(id=z[0], tick=z[1], agent_id=z[2], typ=z[3], text=z[4], wichtigkeit=z[5])
            for z in zeilen
        )

    def routine(self, agent_id: int | None = None) -> dict[str, int]:
        """Gezählte Routineaktionen, für einen Agenten oder für alle zusammen."""
        if agent_id is None:
            zeilen = self._db.execute(
                "SELECT aktion, SUM(anzahl) FROM routine GROUP BY aktion ORDER BY aktion"
            ).fetchall()
        else:
            zeilen = self._db.execute(
                "SELECT aktion, anzahl FROM routine WHERE agent_id = ? ORDER BY aktion",
                (agent_id,),
            ).fetchall()
        return {aktion: int(anzahl) for aktion, anzahl in zeilen}

    def anzahl_erinnerungen(self, agent_id: int | None = None) -> int:
        if agent_id is None:
            return self._db.execute("SELECT COUNT(*) FROM erinnerungen").fetchone()[0]
        return self._db.execute(
            "SELECT COUNT(*) FROM erinnerungen WHERE agent_id = ?", (agent_id,)
        ).fetchone()[0]

    def schliessen(self) -> None:
        self._db.commit()
        self._db.close()
