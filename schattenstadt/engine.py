"""Die Engine: einziger Ort, an dem sich der Weltzustand ändert.

Ablauf eines Ticks:

1. Ereignisse der Welt (Schließung eines Ortes, tägliches Grundeinkommen)
2. Bedürfnisse aller Agenten laufen weiter
3. Jeder Agent entscheidet nacheinander in fester Reihenfolge (nach ID):
   Optionen aufzählen, Policy fragen, Entscheidung prüfen, ausführen,
   Erinnerungen aus dem Ereignis schreiben, Loop-Detektor füttern
4. Invarianten prüfen; eine Verletzung bricht den Lauf ab

Alle Zufälle hängen am Seed der Konfiguration.
"""

from __future__ import annotations

import logging
import random
from collections import Counter, deque
from dataclasses import dataclass

from .actions import Aktion, Beobachtung, Entscheidung, UngueltigeEntscheidung, gueltige_optionen, pruefe
from .agent import Agent, erzeuge_agenten
from .memory import Ereignis, Gedaechtnis
from .policy import Policy, RoutinePolicy
from .world import ANZEIGE, LAEDEN, ORTE, PARK, WOHNUNG, Welt

log = logging.getLogger("schattenstadt")

START_STUNDE = 6  # Tick 0 ist 6 Uhr morgens; ein Tick ist eine Stunde
GRUNDEINKOMMEN_PRO_TAG = 45  # um Mitternacht für Agenten ohne Job
LOHN_PRO_TICK = 15
ARBEIT_ENERGIE = 3
SAETTIGUNG = 50  # um so viel senkt ein Einkauf den Hunger
RUHE_ENERGIE = {WOHNUNG: 10, PARK: 6}
HUNGER_KRITISCH_AB = 90

LOOP_WIEDERHOLUNGEN = 3
LOOP_MAX_LAENGE = 3


@dataclass(frozen=True)
class Konfig:
    agenten: int = 20
    ticks: int = 100
    seed: int = 42
    schliessen: str | None = None
    schliessen_bei: int | None = None
    db: str = ":memory:"
    loop_sperre: int = 10  # so viele Ticks übernimmt die RoutinePolicy nach einem Loop


@dataclass(frozen=True)
class Einkaufsweg:
    """Ein Hinweg zum Laden, der mit einem Einkauf endete."""

    tick: int
    agent_id: int
    laden: str
    minuten: int
    auto: bool


@dataclass(frozen=True)
class Protokolleintrag:
    tick: int
    agent_id: int | None
    art: str
    text: str


@dataclass(frozen=True)
class Schritt:
    """Ein ausgeführter Schritt, wie ihn der Loop-Detektor sieht."""

    action: Aktion
    target: str
    ort_vorher: str
    wirkung: bool  # hat die Aktion Hunger, Energie oder Geld verändert? (Wege zählen nicht)


@dataclass(frozen=True)
class Zug:
    """Eine ausgeführte Entscheidung samt Herkunft, für Auswertung und Tests."""

    tick: int
    agent_id: int
    action: Aktion
    target: str
    quelle: str  # Name der Policy, "routine_fallback" oder "routine_loop"


class InvariantenVerletzung(RuntimeError):
    pass


def erkenne_loop(
    verlauf: list[Schritt] | deque[Schritt],
    wiederholungen: int = LOOP_WIEDERHOLUNGEN,
    max_laenge: int = LOOP_MAX_LAENGE,
) -> int | None:
    """Länge der Aktionsfolge, die sich am Ende des Verlaufs wiederholt, sonst None.

    Ein Loop liegt vor, wenn dieselbe Folge von (Aktion, Ziel) ``wiederholungen``-
    mal direkt hintereinander vorkommt und sich dabei der Zustand nicht ändert:
    Keine Aktion im Fenster hat Hunger, Energie oder Geld verändert, und jede
    Wiederholung beginnt am selben Ort. Nicht als Zustandsänderung zählen das
    passive Weiterlaufen der Bedürfnisse und die Energie, die ein Weg kostet:
    Ein Weg, der wieder am Ausgangsort endet, ist verschwendet.
    """
    schritte = list(verlauf)
    for laenge in range(1, max_laenge + 1):
        n = laenge * wiederholungen
        if len(schritte) < n:
            break
        fenster = schritte[-n:]
        if any(s.wirkung for s in fenster):
            continue
        bloecke = [fenster[i * laenge : (i + 1) * laenge] for i in range(wiederholungen)]
        folgen = {tuple((s.action, s.target) for s in block) for block in bloecke}
        starts = {block[0].ort_vorher for block in bloecke}
        if len(folgen) == 1 and len(starts) == 1:
            return laenge
    return None


class Engine:
    def __init__(self, konfig: Konfig, policy: Policy | None = None) -> None:
        if konfig.agenten < 1 or konfig.ticks < 1:
            raise ValueError("agenten und ticks müssen mindestens 1 sein")
        if (konfig.schliessen is None) != (konfig.schliessen_bei is None):
            raise ValueError("schliessen und schliessen_bei nur zusammen angeben")
        if konfig.schliessen is not None and konfig.schliessen not in ORTE:
            raise ValueError(f"unbekannter Ort: {konfig.schliessen}")
        if konfig.schliessen_bei is not None and not 0 <= konfig.schliessen_bei < konfig.ticks:
            raise ValueError(f"schliessen_bei muss zwischen 0 und {konfig.ticks - 1} liegen")

        self.konfig = konfig
        self.rng = random.Random(konfig.seed)
        self.welt = Welt()
        self.agenten: list[Agent] = erzeuge_agenten(konfig.agenten, self.rng)
        for agent in self.agenten:
            self.welt.platziere(agent.id, agent.ort)
        self.gedaechtnis = Gedaechtnis(konfig.db)
        self.routine = RoutinePolicy(konfig.seed)
        self.policy: Policy = policy or self.routine

        self.tick = 0
        self.zuege: list[Zug] = []
        self.einkaufswege: list[Einkaufsweg] = []
        self.protokoll: list[Protokolleintrag] = []
        self.zaehler: Counter[str] = Counter()

        self._verlauf: dict[int, deque[Schritt]] = {
            a.id: deque(maxlen=LOOP_WIEDERHOLUNGEN * LOOP_MAX_LAENGE) for a in self.agenten
        }
        self._routine_bis: dict[int, int] = {}  # Agent -> erster Tick ohne Zwangs-Routine
        self._letzte_aktion: dict[int, Aktion] = {}
        self._anreise: dict[int, int] = {}  # Minuten des Hinwegs zum aktuellen Laden
        self._letzter_laden: dict[int, str] = {}
        self._hunger_kritisch: set[int] = set()

        self.pruefe_invarianten()

    # --- Ablauf ----------------------------------------------------------

    def laufe(self) -> Engine:
        while self.tick < self.konfig.ticks:
            self.schritt()
        return self

    def schritt(self) -> None:
        t = self.tick
        stunde = (START_STUNDE + t) % 24

        if self.konfig.schliessen is not None and t == self.konfig.schliessen_bei:
            self._schliesse(self.konfig.schliessen)
        if stunde == 0:
            for agent in self.agenten:
                if agent.job is None:
                    agent.geld += GRUNDEINKOMMEN_PRO_TAG

        for agent in self.agenten:
            agent.weiterlaufen()

        for agent in self.agenten:
            self._zug(agent, stunde)

        self.pruefe_invarianten()
        self.tick += 1

    def schliessen(self) -> None:
        """Schließt die Datenbank des Gedächtnisses."""
        self.gedaechtnis.schliessen()

    # --- ein Agent ist am Zug -------------------------------------------

    def _zug(self, agent: Agent, stunde: int) -> None:
        optionen = gueltige_optionen(self.welt, agent)
        if not optionen:
            self._protokolliere(agent.id, "keine_option", f"keine gültige Aktion am Ort {agent.ort}")
            return
        beobachtung = Beobachtung(
            tick=self.tick,
            stunde=stunde,
            agent_id=agent.id,
            ort=agent.ort,
            hunger=agent.hunger,
            energie=agent.energie,
            geld=agent.geld,
            job=agent.job,
            auto=agent.auto,
            optionen=optionen,
            erinnerungen=self.gedaechtnis.abrufen(agent.id),
            geschlossen=self.welt.geschlossene_orte(),
            letzte_aktion=self._letzte_aktion.get(agent.id),
        )
        entscheidung, quelle = self._entscheide(agent, beobachtung)
        ort_vorher = agent.ort
        wirkung = self._fuehre_aus(agent, entscheidung)

        self.zaehler["entscheidungen"] += 1
        self._letzte_aktion[agent.id] = entscheidung.action
        self.zuege.append(Zug(self.tick, agent.id, entscheidung.action, entscheidung.target, quelle))
        self._hunger_pruefen(agent)
        self._loop_pruefen(agent, Schritt(entscheidung.action, entscheidung.target, ort_vorher, wirkung))

    def _entscheide(self, agent: Agent, b: Beobachtung) -> tuple[Entscheidung, str]:
        if self.policy is self.routine:
            # Eine ungültige Routine-Entscheidung ist ein Programmfehler und
            # darf den Lauf abbrechen.
            return pruefe(self.routine.entscheide(b), b), self.routine.name
        if self._routine_bis.get(agent.id, -1) > self.tick:
            return pruefe(self.routine.entscheide(b), b), "routine_loop"

        rueckmeldung: str | None = None
        for versuch in (1, 2):  # erster Versuch und genau ein Retry
            try:
                return pruefe(self.policy.entscheide(b, rueckmeldung), b), self.policy.name
            except UngueltigeEntscheidung as fehler:
                art, rueckmeldung = "ungueltig", str(fehler)
            except Exception as fehler:  # API-, Netzwerk- oder Stub-Fehler der Policy
                art, rueckmeldung = "policy_fehler", f"{type(fehler).__name__}: {fehler}"
            self.zaehler[art] += 1
            self._protokolliere(agent.id, art, f"Versuch {versuch}: {rueckmeldung}", logging.DEBUG)

        self.zaehler["fallback"] += 1
        self._protokolliere(agent.id, "fallback", "zwei Versuche ungültig, RoutinePolicy übernimmt", logging.DEBUG)
        return pruefe(self.routine.entscheide(b), b), "routine_fallback"

    def _fuehre_aus(self, agent: Agent, e: Entscheidung) -> bool:
        """Führt eine geprüfte Entscheidung aus. Rückgabe: ob die Aktion Hunger,
        Energie oder Geld verändert hat. Ein Weg zählt dabei nicht, auch wenn er
        Energie kostet (siehe ``erkenne_loop``)."""
        t = self.tick
        if e.action == Aktion.MOVE:
            minuten = self.welt.wegzeit(agent.ort, e.target, agent.auto)
            self.welt.bewege(agent.id, agent.ort, e.target)
            agent.ort = e.target
            agent.energie = max(0, agent.energie - minuten // 10)
            if e.target in LAEDEN:
                self._anreise[agent.id] = minuten
            else:
                self._anreise.pop(agent.id, None)
            self.gedaechtnis.zaehle_routine(agent.id, "move")
            return False

        if e.action == Aktion.BUY:
            preis = LAEDEN[agent.ort]
            agent.geld -= preis
            agent.hunger = max(0, agent.hunger - SAETTIGUNG)
            minuten = self._anreise.pop(agent.id, None)
            if minuten is not None:  # weitere Einkäufe beim selben Besuch sind kein neuer Weg
                self.einkaufswege.append(Einkaufsweg(t, agent.id, agent.ort, minuten, agent.auto))
            weg = f"{minuten} min" if minuten is not None else "-"
            vorher = self._letzter_laden.get(agent.id)
            if vorher is not None and vorher != agent.ort:
                self.gedaechtnis.merke(
                    t, agent.id, Ereignis.LADEN_GEWECHSELT,
                    laden=ANZEIGE[agent.ort], vorher=ANZEIGE[vorher], preis=preis, weg=weg,
                )
            else:
                self.gedaechtnis.merke(t, agent.id, Ereignis.EINKAUF, laden=ANZEIGE[agent.ort], preis=preis, weg=weg)
            self._letzter_laden[agent.id] = agent.ort
            return True

        if e.action == Aktion.WORK:
            agent.geld += LOHN_PRO_TICK
            agent.energie = max(0, agent.energie - ARBEIT_ENERGIE)
            self.gedaechtnis.zaehle_routine(agent.id, "work")
            return True

        if e.action == Aktion.REST:
            vorher = agent.energie
            agent.energie = min(100, agent.energie + RUHE_ENERGIE[agent.ort])
            self.gedaechtnis.zaehle_routine(agent.id, "rest")
            return agent.energie != vorher

        if e.action == Aktion.TALK:
            andere = [a for a in self.welt.anwesende(agent.ort) if a != agent.id]
            partner = random.Random(f"{self.konfig.seed}:talk:{agent.id}:{t}").choice(andere)
            ort = ANZEIGE[agent.ort]
            self.gedaechtnis.merke(t, agent.id, Ereignis.GESPRAECH, partner=partner, ort=ort)
            self.gedaechtnis.merke(t, partner, Ereignis.GESPRAECH, partner=agent.id, ort=ort)
            return False

        raise AssertionError(f"unbehandelte Aktion {e.action}")

    # --- Ereignisse, Loop-Detektor, Invarianten --------------------------

    def _schliesse(self, ort: str) -> None:
        self.welt.schliesse(ort)
        self._protokolliere(None, "schliessung", f"{ort} geschlossen", logging.INFO)
        for agent in self.agenten:
            self.gedaechtnis.merke(self.tick, agent.id, Ereignis.ORT_GESCHLOSSEN, ort=ANZEIGE[ort], seit=self.tick)

    def _hunger_pruefen(self, agent: Agent) -> None:
        if agent.hunger >= HUNGER_KRITISCH_AB:
            if agent.id not in self._hunger_kritisch:
                self._hunger_kritisch.add(agent.id)
                self.gedaechtnis.merke(
                    self.tick, agent.id, Ereignis.HUNGER_KRITISCH, hunger=agent.hunger, ort=ANZEIGE[agent.ort]
                )
        else:
            self._hunger_kritisch.discard(agent.id)

    def _loop_pruefen(self, agent: Agent, schritt: Schritt) -> None:
        verlauf = self._verlauf[agent.id]
        verlauf.append(schritt)
        laenge = erkenne_loop(verlauf)
        if laenge is None:
            return
        folge = " -> ".join(f"{s.action}:{s.target}" for s in list(verlauf)[-laenge:])
        befund = f"Folge [{folge}] {LOOP_WIEDERHOLUNGEN}x ohne Zustandsänderung"
        verlauf.clear()
        if self.policy is self.routine or self._routine_bis.get(agent.id, -1) > self.tick:
            # Die Routine selbst dreht sich im Kreis. Ein Eingriff würde nichts
            # ändern; das ist ein Fehler in den Regeln und wird so gemeldet.
            self.zaehler["loop_routine"] += 1
            self._protokolliere(agent.id, "loop_routine", f"{befund}, Fehler in der RoutinePolicy", logging.ERROR)
            return
        self._routine_bis[agent.id] = self.tick + 1 + self.konfig.loop_sperre
        self.zaehler["loop"] += 1
        self._protokolliere(
            agent.id,
            "loop",
            f"{befund}, RoutinePolicy bis einschließlich Tick {self.tick + self.konfig.loop_sperre}",
            logging.WARNING,
        )

    def pruefe_invarianten(self) -> None:
        """Geld nie negativ, Bedürfnisse im Bereich, jeder Agent an genau einem Ort."""
        fehler: list[str] = []
        vorkommen: Counter[int] = Counter()
        for ort in ORTE:
            vorkommen.update(self.welt.anwesend[ort])
        ids = {a.id for a in self.agenten}
        for agent in self.agenten:
            if agent.geld < 0:
                fehler.append(f"Agent {agent.id}: Geld negativ ({agent.geld})")
            if not 0 <= agent.hunger <= 100:
                fehler.append(f"Agent {agent.id}: Hunger außerhalb 0-100 ({agent.hunger})")
            if not 0 <= agent.energie <= 100:
                fehler.append(f"Agent {agent.id}: Energie außerhalb 0-100 ({agent.energie})")
            if vorkommen[agent.id] != 1:
                fehler.append(f"Agent {agent.id}: an {vorkommen[agent.id]} Orten statt an genau einem")
            elif agent.ort not in self.welt.anwesend or agent.id not in self.welt.anwesend[agent.ort]:
                fehler.append(f"Agent {agent.id}: Ort '{agent.ort}' passt nicht zur Belegung der Welt")
        for fremd in sorted(set(vorkommen) - ids):
            fehler.append(f"unbekannter Agent {fremd} in der Belegung")
        if fehler:
            raise InvariantenVerletzung(f"Tick {self.tick}: " + "; ".join(fehler))

    def _protokolliere(self, agent_id: int | None, art: str, text: str, level: int = logging.INFO) -> None:
        self.protokoll.append(Protokolleintrag(self.tick, agent_id, art, text))
        wer = "Welt" if agent_id is None else f"Agent {agent_id}"
        log.log(level, "Tick %d, %s: %s", self.tick, wer, text)
