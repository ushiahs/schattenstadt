"""Agenten: ein Agent steht für einen Haushalt im Viertel.

Bedürfnisse sind ganze Zahlen und laufen jeden Tick weiter:

* ``hunger``  0 (satt) bis 100 (ausgehungert), steigt jeden Tick
* ``energie`` 0 (erschöpft) bis 100 (ausgeruht), sinkt jeden Tick
* ``geld``    Euro, sinkt jeden Tick um die Fixkosten, nie negativ

Agenten ändern ihren Zustand nicht selbst. Das tut die Engine, entweder über
``weiterlaufen()`` oder beim Ausführen einer geprüften Entscheidung.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .world import ARBEIT, WOHNUNG

HUNGER_PRO_TICK = 3
ENERGIE_PRO_TICK = 2
FIXKOSTEN_PRO_TICK = 1

ANTEIL_MIT_AUTO = 0.4
ANTEIL_MIT_JOB = 0.7


@dataclass
class Agent:
    id: int
    wohnort: str
    job: str | None
    auto: bool
    ort: str
    hunger: int
    energie: int
    geld: int

    def weiterlaufen(self) -> None:
        """Ein Tick vergeht: Hunger steigt, Energie sinkt, Fixkosten fallen an.

        Fixkosten werden nur bis zum Kontostand 0 abgebucht. Schulden
        bildet der Prototyp nicht ab.
        """
        self.hunger = min(100, self.hunger + HUNGER_PRO_TICK)
        self.energie = max(0, self.energie - ENERGIE_PRO_TICK)
        self.geld = max(0, self.geld - FIXKOSTEN_PRO_TICK)


def erzeuge_agenten(anzahl: int, rng: random.Random) -> list[Agent]:
    """Erzeugt Agenten mit zufälligen, aber durch ``rng`` festgelegten Werten.

    Alle wohnen am Ort ``wohnung``; eine echte Karte mit verteilten
    Wohnorten gibt es im Prototyp noch nicht.
    """
    agenten = []
    for agent_id in range(anzahl):
        auto = rng.random() < ANTEIL_MIT_AUTO
        job = ARBEIT if rng.random() < ANTEIL_MIT_JOB else None
        agenten.append(
            Agent(
                id=agent_id,
                wohnort=WOHNUNG,
                job=job,
                auto=auto,
                ort=WOHNUNG,
                hunger=rng.randint(0, 40),
                energie=rng.randint(60, 100),
                geld=rng.randint(20, 150),
            )
        )
    return agenten
