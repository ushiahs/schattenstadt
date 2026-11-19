from schattenstadt.actions import Beobachtung
from schattenstadt.engine import Engine, Konfig
from schattenstadt.policy import LLMPolicy, Policy, RoutinePolicy
from schattenstadt.world import SUPERMARKT


def ungueltig(b: Beobachtung) -> dict:
    return {"agent_id": b.agent_id, "tick": b.tick, "action": "move", "target": "rathaus", "reason": "falsch"}


class ErstFalschPolicy(Policy):
    """Erster Versuch ungültig, der Retry (mit Rückmeldung) gültig."""

    name = "erst_falsch"

    def __init__(self):
        self.routine = RoutinePolicy(seed=0)
        self.rueckmeldungen = []

    def entscheide(self, b, rueckmeldung=None):
        if rueckmeldung is None:
            return ungueltig(b)
        self.rueckmeldungen.append(rueckmeldung)
        return self.routine.entscheide(b).model_dump_json()


class ImmerFalschPolicy(Policy):
    name = "immer_falsch"

    def __init__(self):
        self.aufrufe = 0

    def entscheide(self, b, rueckmeldung=None):
        self.aufrufe += 1
        return ungueltig(b)


def test_retry_mit_rueckmeldung_wird_angenommen():
    policy = ErstFalschPolicy()
    engine = Engine(Konfig(agenten=3, ticks=10), policy=policy).laufe()
    assert engine.zaehler["entscheidungen"] == 30
    assert engine.zaehler["ungueltig"] == 30
    assert engine.zaehler["fallback"] == 0
    assert {z.quelle for z in engine.zuege} == {"erst_falsch"}
    assert all("unbekannter Ort 'rathaus'" in r for r in policy.rueckmeldungen)


def test_nach_einem_retry_uebernimmt_routine():
    policy = ImmerFalschPolicy()
    engine = Engine(Konfig(agenten=3, ticks=10), policy=policy).laufe()
    assert policy.aufrufe == 2 * 30  # genau ein Retry je Entscheidung
    assert engine.zaehler["fallback"] == 30
    assert {z.quelle for z in engine.zuege} == {"routine_fallback"}


def test_llm_stub_laeuft_ohne_api_key_ueber_routine():
    engine = Engine(Konfig(schliessen=SUPERMARKT, schliessen_bei=50), policy=LLMPolicy()).laufe()
    assert engine.tick == 100
    assert engine.zaehler["policy_fehler"] == 2 * engine.zaehler["fallback"]
    assert any("NotImplementedError" in p.text for p in engine.protokoll)


def test_llm_prompt_enthaelt_optionen_erinnerungen_und_schema():
    engine = Engine(Konfig(agenten=2, ticks=60, schliessen=SUPERMARKT, schliessen_bei=50)).laufe()
    from schattenstadt.actions import gueltige_optionen

    agent = engine.agenten[0]
    b = Beobachtung(
        tick=engine.tick, stunde=12, agent_id=0, ort=agent.ort, hunger=agent.hunger,
        energie=agent.energie, geld=agent.geld, job=agent.job, auto=agent.auto,
        optionen=gueltige_optionen(engine.welt, agent),
        erinnerungen=engine.gedaechtnis.abrufen(0),
        geschlossen=engine.welt.geschlossene_orte(),
    )
    prompt = LLMPolicy().prompt(b, rueckmeldung="Ziel war geschlossen")
    for option in b.optionen:
        assert f'"target": "{option.target}"' in prompt
    assert "Supermarkt ist seit Tick 50 geschlossen." in prompt
    assert '"memory_ids"' in prompt
    assert "Ziel war geschlossen" in prompt


def test_routine_ist_deterministisch_je_beobachtung():
    engine = Engine(Konfig(agenten=5, ticks=30))
    engine.laufe()
    from schattenstadt.actions import gueltige_optionen

    agent = engine.agenten[2]
    b = Beobachtung(
        tick=30, stunde=12, agent_id=2, ort=agent.ort, hunger=agent.hunger, energie=agent.energie,
        geld=agent.geld, job=agent.job, auto=agent.auto, optionen=gueltige_optionen(engine.welt, agent),
    )
    assert RoutinePolicy(7).entscheide(b) == RoutinePolicy(7).entscheide(b)
