"""Aktionen, Entscheidungs-Schema und Prüfung.

Die Engine zählt für jeden Agenten die gültigen Optionen auf (``gueltige_optionen``)
und gibt sie der Policy als ``Beobachtung``. Eine Policy liefert eine
``Entscheidung`` (als Objekt, Dict oder JSON-Text). ``pruefe()`` validiert sie
gegen die Beobachtung: Gültig ist nur, was unter den Optionen steht. Damit
ist ``target`` immer ein erreichbarer, offener Ort.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, model_validator

from .agent import Agent
from .memory import Erinnerung
from .world import LAEDEN, ORTE, RASTORTE, Welt


class Aktion(StrEnum):
    MOVE = "move"
    BUY = "buy"
    WORK = "work"
    REST = "rest"
    TALK = "talk"


class Option(BaseModel):
    """Eine gültige Aktion samt Ziel, wie die Engine sie anbietet."""

    model_config = ConfigDict(frozen=True)

    action: Aktion
    target: str
    minuten: int | None = None  # Wegzeit bei move
    preis: int | None = None  # Preis eines Einkaufs, wenn das Ziel ein Laden ist


class Beobachtung(BaseModel):
    """Alles, was eine Policy für eine Entscheidung sieht."""

    model_config = ConfigDict(frozen=True)

    tick: int
    stunde: int
    agent_id: int
    ort: str
    hunger: int
    energie: int
    geld: int
    job: str | None
    auto: bool
    optionen: tuple[Option, ...]
    erinnerungen: tuple[Erinnerung, ...] = ()
    geschlossen: tuple[str, ...] = ()
    letzte_aktion: Aktion | None = None

    def optionen_fuer(self, action: Aktion) -> list[Option]:
        return [o for o in self.optionen if o.action == action]

    def option(self, action: Aktion, target: str) -> Option | None:
        for o in self.optionen:
            if o.action == action and o.target == target:
                return o
        return None


class UngueltigeEntscheidung(ValueError):
    """Die Entscheidung passt nicht zum Schema oder nicht zur Lage."""


class Entscheidung(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: int = Field(ge=0)
    tick: int = Field(ge=0)
    action: Aktion
    target: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=300)
    memory_ids: list[int] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def _gegen_beobachtung_pruefen(self, info: ValidationInfo) -> Entscheidung:
        beobachtung = (info.context or {}).get("beobachtung")
        if beobachtung is None:
            return self
        b: Beobachtung = beobachtung
        if self.agent_id != b.agent_id:
            raise ValueError(f"agent_id {self.agent_id} passt nicht, erwartet {b.agent_id}")
        if self.tick != b.tick:
            raise ValueError(f"tick {self.tick} passt nicht, erwartet {b.tick}")
        if b.option(self.action, self.target) is None:
            raise ValueError(_warum_ungueltig(self.action, self.target, b))
        bekannt = {e.id for e in b.erinnerungen}
        fremd = sorted(set(self.memory_ids) - bekannt)
        if fremd:
            raise ValueError(f"memory_ids {fremd} gehören nicht zu den gezeigten Erinnerungen")
        return self


def _warum_ungueltig(action: Aktion, target: str, b: Beobachtung) -> str:
    if target not in ORTE:
        grund = f"unbekannter Ort '{target}'"
    elif target in b.geschlossen:
        grund = f"Ort '{target}' ist geschlossen"
    elif not b.optionen_fuer(action):
        grund = f"Aktion '{action}' ist am Ort '{b.ort}' gerade nicht möglich"
    else:
        grund = f"Ziel '{target}' ist für '{action}' nicht gültig"
    gueltig = ", ".join(f"{o.action}:{o.target}" for o in b.optionen)
    return f"{grund}; gültig sind: {gueltig}"


def pruefe(roh: Entscheidung | dict | str | bytes, beobachtung: Beobachtung) -> Entscheidung:
    """Validiert eine Entscheidung gegen Schema und Lage.

    Auch fertige ``Entscheidung``-Objekte werden neu validiert: Die Engine
    vertraut keiner Policy.
    """
    kontext = {"beobachtung": beobachtung}
    try:
        if isinstance(roh, Entscheidung):
            roh = roh.model_dump(mode="json")
        if isinstance(roh, (str, bytes)):
            return Entscheidung.model_validate_json(roh, context=kontext)
        return Entscheidung.model_validate(roh, context=kontext)
    except ValidationError as fehler:
        meldungen = "; ".join(
            f"{'.'.join(str(teil) for teil in e['loc']) or 'entscheidung'}: {e['msg']}"
            for e in fehler.errors()
        )
        raise UngueltigeEntscheidung(meldungen) from fehler


def gueltige_optionen(welt: Welt, agent: Agent) -> tuple[Option, ...]:
    """Alle Aktionen, die der Agent jetzt ausführen darf."""
    optionen: list[Option] = []
    hier = agent.ort
    for ziel in ORTE:
        if ziel != hier and welt.ist_offen(ziel) and welt.erreichbar(hier, ziel):
            optionen.append(
                Option(
                    action=Aktion.MOVE,
                    target=ziel,
                    minuten=welt.wegzeit(hier, ziel, agent.auto),
                    preis=LAEDEN.get(ziel),
                )
            )
    if welt.ist_offen(hier):
        preis = LAEDEN.get(hier)
        if preis is not None and agent.geld >= preis:
            optionen.append(Option(action=Aktion.BUY, target=hier, preis=preis))
        if agent.job == hier:
            optionen.append(Option(action=Aktion.WORK, target=hier))
        if hier in RASTORTE:
            optionen.append(Option(action=Aktion.REST, target=hier))
        if welt.hat_andere(hier, agent.id):
            optionen.append(Option(action=Aktion.TALK, target=hier))
    return tuple(optionen)
