"""Policies: wer entscheidet, was ein Agent als Nächstes tut.

Eine Policy sieht eine ``Beobachtung`` und liefert eine Entscheidung. Sie
ändert nie den Weltzustand; das tut allein die Engine nach der Prüfung.

* ``RoutinePolicy`` entscheidet regelbasiert und ist der Standard.
* ``LLMPolicy`` ist ein Stub: Der Prompt wird gebaut, der API-Call fehlt.
"""

from __future__ import annotations

import json
import random
from abc import ABC, abstractmethod

from .actions import Aktion, Beobachtung, Entscheidung, Option
from .agent import FIXKOSTEN_PRO_TICK
from .memory import Ereignis
from .world import WOHNUNG, PARK

# Schwellen der Routine
KAUFEN_AB = 40  # an einem Laden wird ab diesem Hunger eingekauft
HUNGRIG_AB = 60  # ab diesem Hunger geht der Agent einkaufen
MUEDE_BIS = 15  # bis zu dieser Energie geht der Agent ruhen
SPARMODUS_UNTER = 40  # unter diesem Kontostand zählt der Preis vor der Wegzeit
ARBEITSBEGINN, ARBEITSENDE = 8, 16
NACHT_AB, NACHT_BIS = 22, 6
PARK_GEHEN = 0.15  # Chance je freier Stunde, von der Wohnung in den Park zu gehen
PARK_VERLASSEN = 0.3  # Chance je Stunde im Park, nach Hause zu gehen


class Policy(ABC):
    name: str = "policy"

    @abstractmethod
    def entscheide(
        self, beobachtung: Beobachtung, rueckmeldung: str | None = None
    ) -> Entscheidung | dict | str:
        """Liefert eine Entscheidung.

        ``rueckmeldung`` ist beim zweiten Versuch der Grund, warum der erste
        ungültig war. Die Engine prüft jede Rückgabe mit ``actions.pruefe``.
        """


def ist_nacht(stunde: int) -> bool:
    return stunde >= NACHT_AB or stunde < NACHT_BIS


def ist_arbeitszeit(stunde: int) -> bool:
    return ARBEITSBEGINN <= stunde < ARBEITSENDE


class RoutinePolicy(Policy):
    """Feste Regeln, nach Priorität: einkaufen, ruhen, arbeiten, Nachtruhe, Freizeit.

    Zufall (nur in der Freizeit) kommt aus einem Generator, der aus Seed,
    Agent und Tick abgeleitet wird. Dieselbe Beobachtung ergibt deshalb immer
    dieselbe Entscheidung, egal in welcher Reihenfolge Agenten entscheiden.
    """

    name = "routine"

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def entscheide(self, beobachtung: Beobachtung, rueckmeldung: str | None = None) -> Entscheidung:
        b = beobachtung
        rng = random.Random(f"{self.seed}:routine:{b.agent_id}:{b.tick}")
        action, target, reason, memory_ids = self._waehle(b, rng)
        return Entscheidung(
            agent_id=b.agent_id,
            tick=b.tick,
            action=action,
            target=target,
            reason=reason,
            memory_ids=memory_ids,
        )

    def _waehle(self, b: Beobachtung, rng: random.Random) -> tuple[Aktion, str, str, list[int]]:
        schliessungen = [e.id for e in b.erinnerungen if e.typ == Ereignis.ORT_GESCHLOSSEN]

        # 1. Am Laden und hungrig genug: einkaufen.
        kauf = b.optionen_fuer(Aktion.BUY)
        if kauf and b.hunger >= KAUFEN_AB:
            return Aktion.BUY, kauf[0].target, f"Hunger {b.hunger}, Laden offen", schliessungen

        # 2. Hungrig: zum passenden offenen Laden.
        if b.hunger >= HUNGRIG_AB:
            laden = self._laden_waehlen(b)
            if laden is not None:
                grund = f"Hunger {b.hunger}, {laden.target} ({laden.minuten} min, {laden.preis} €)"
                return Aktion.MOVE, laden.target, grund, schliessungen

        # 3. Erschöpft: ausruhen, sonst nach Hause.
        if b.energie <= MUEDE_BIS:
            if b.option(Aktion.REST, b.ort):
                return Aktion.REST, b.ort, f"Energie {b.energie}", []
            if b.option(Aktion.MOVE, WOHNUNG):
                return Aktion.MOVE, WOHNUNG, f"Energie {b.energie}, nach Hause", []

        # 4. Arbeitszeit: zur Arbeit bzw. arbeiten.
        if b.job is not None and ist_arbeitszeit(b.stunde):
            if b.option(Aktion.WORK, b.job):
                return Aktion.WORK, b.job, "Arbeitszeit", []
            if b.option(Aktion.MOVE, b.job):
                return Aktion.MOVE, b.job, "Arbeitszeit, zur Arbeit", []

        # 5. Nacht: zu Hause schlafen.
        if ist_nacht(b.stunde):
            if b.option(Aktion.REST, WOHNUNG):
                return Aktion.REST, WOHNUNG, "Nachtruhe", []
            if b.option(Aktion.MOVE, WOHNUNG):
                return Aktion.MOVE, WOHNUNG, "Nacht, nach Hause", []

        # 6. Freizeit.
        if b.ort == PARK:
            if b.option(Aktion.TALK, PARK) and b.letzte_aktion != Aktion.TALK:
                return Aktion.TALK, PARK, "Bekannte im Park", []
            if rng.random() < PARK_VERLASSEN and b.option(Aktion.MOVE, WOHNUNG):
                return Aktion.MOVE, WOHNUNG, "Freizeit, nach Hause", []
            if b.option(Aktion.REST, PARK):
                return Aktion.REST, PARK, "Freizeit im Park", []
        if b.ort == WOHNUNG:
            # Nach dem Heimkommen erst einmal ausruhen, sonst kann ein Pendeln
            # Wohnung-Park ohne jede Wirkung entstehen (das wäre ein Loop).
            gerade_angekommen = b.letzte_aktion == Aktion.MOVE
            if not gerade_angekommen and rng.random() < PARK_GEHEN and b.option(Aktion.MOVE, PARK):
                return Aktion.MOVE, PARK, "Freizeit, in den Park", []
            if b.option(Aktion.REST, WOHNUNG):
                return Aktion.REST, WOHNUNG, "Freizeit zu Hause", []

        # 7. Sonst nach Hause, und wenn das nicht geht, die erste Option.
        if b.option(Aktion.MOVE, WOHNUNG):
            return Aktion.MOVE, WOHNUNG, "nichts zu tun, nach Hause", []
        erste = b.optionen[0]
        return erste.action, erste.target, "keine passende Regel, erste gültige Option", []

    @staticmethod
    def _laden_waehlen(b: Beobachtung) -> Option | None:
        """Nächster offener Laden, den sich der Agent leisten kann.

        Im Sparmodus (wenig Geld) zählt der Preis vor der Wegzeit.
        """
        kandidaten = [
            o
            for o in b.optionen_fuer(Aktion.MOVE)
            if o.preis is not None and b.geld - FIXKOSTEN_PRO_TICK >= o.preis
        ]
        if not kandidaten:
            return None
        if b.geld < SPARMODUS_UNTER:
            return min(kandidaten, key=lambda o: (o.preis, o.minuten, o.target))
        return min(kandidaten, key=lambda o: (o.minuten, o.preis, o.target))


class LLMPolicy(Policy):
    """Stub für eine Policy, bei der ein Sprachmodell entscheidet.

    Der Prompt mit Lage, gültigen Optionen, Erinnerungen und JSON-Schema wird
    schon gebaut. Der eigentliche API-Call fehlt noch. Bis dahin wirft
    ``entscheide`` ``NotImplementedError``; die Engine wertet das wie eine
    ungültige Entscheidung und übernimmt nach einem Retry mit der RoutinePolicy.
    """

    name = "llm"

    def __init__(self, modell: str | None = None) -> None:
        self.modell = modell

    def prompt(self, beobachtung: Beobachtung, rueckmeldung: str | None = None) -> str:
        b = beobachtung
        lage = {
            "tick": b.tick,
            "stunde": b.stunde,
            "agent_id": b.agent_id,
            "ort": b.ort,
            "hunger": b.hunger,
            "energie": b.energie,
            "geld": b.geld,
            "job": b.job,
            "auto": b.auto,
            "geschlossen": list(b.geschlossen),
        }
        optionen = [o.model_dump(mode="json", exclude_none=True) for o in b.optionen]
        erinnerungen = [
            {"id": e.id, "tick": e.tick, "text": e.text, "wichtigkeit": e.wichtigkeit}
            for e in b.erinnerungen
        ]
        teile = [
            "Du bist ein Bewohner eines Stadtviertels. Wähle genau eine der gültigen Optionen.",
            "Lage: " + json.dumps(lage, ensure_ascii=False),
            "Gültige Optionen (action, target): " + json.dumps(optionen, ensure_ascii=False),
            "Erinnerungen (nur diese IDs dürfen in memory_ids stehen): "
            + json.dumps(erinnerungen, ensure_ascii=False),
            "Antworte nur mit JSON nach diesem Schema: "
            + json.dumps(Entscheidung.model_json_schema(), ensure_ascii=False),
        ]
        if rueckmeldung:
            teile.append("Dein letzter Vorschlag war ungültig: " + rueckmeldung)
        return "\n\n".join(teile)

    def entscheide(self, beobachtung: Beobachtung, rueckmeldung: str | None = None) -> str:
        prompt = self.prompt(beobachtung, rueckmeldung)
        # TODO(LLM-Anbindung): Hier den API-Call einsetzen.
        #   Eingabe: `prompt` (Lage, gültige Optionen, Erinnerungen, JSON-Schema
        #   der Entscheidung). Modell und Zugangsdaten über self.modell und
        #   Umgebungsvariablen, nie im Code.
        #   Rückgabe: die rohe JSON-Antwort des Modells als String. Nicht selbst
        #   validieren oder reparieren: Das macht die Engine mit actions.pruefe()
        #   und schickt bei einem Fehler die Rückmeldung für den einen Retry.
        #   Netzwerk- oder API-Fehler einfach als Exception durchreichen; die
        #   Engine zählt sie als Fehlversuch.
        del prompt
        raise NotImplementedError("LLMPolicy ist ein Stub: der API-Call fehlt noch (TODO in policy.py)")
