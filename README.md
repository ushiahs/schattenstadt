# Schattenstadt

Agenten-Simulation eines Stadtviertels.

> **Status: früher Prototyp.** 20 Agenten, 5 Orte, keine echte Karte. Die
> Agenten entscheiden derzeit **regelbasiert** (`RoutinePolicy`). Ein
> Sprachmodell ist noch nicht angebunden; `LLMPolicy` ist ein Stub. Die Zahlen
> unten zeigen, dass der Loop stabil läuft, nicht, wie ein echtes Viertel
> reagiert.

## Kernfrage

Wenn der einzige Supermarkt schließt: Wie viele Haushalte brauchen danach
länger zum Einkaufen, und wen trifft es am härtesten?

## Grundprinzip

Das LLM entscheidet, der Code regiert.

- Den Weltzustand ändert ausschließlich die Engine.
- Ein Agent wählt nur aus den gültigen Aktionen, die die Engine ihm anbietet.
  Die Engine prüft jede Entscheidung und führt sie erst dann aus.
- Erinnerungen schreibt die Engine aus echten Ereignissen. Text des Modells
  (etwa die Begründung `reason`) landet nie im Gedächtnis.

## Architektur

Jeder Tick ist eine Stunde. Zu Beginn eines Ticks wendet die Engine Ereignisse
an (Schließung, tägliches Grundeinkommen) und lässt die Bedürfnisse aller
Agenten weiterlaufen. Dann ist jeder Agent nacheinander am Zug: Die Engine
zählt seine gültigen Optionen auf, fragt die Policy, prüft die Antwort gegen
das Schema und die Optionen, führt sie aus und schreibt Erinnerungen. Am Ende
jedes Ticks prüft sie die Invarianten und bricht bei einer Verletzung ab.

| Datei | Inhalt |
|---|---|
| `schattenstadt/world.py` | Graph (networkx) mit 5 Orten; Wegzeiten zu Fuß und mit Auto als Kantengewichte; Orte schließbar; Belegung der Orte |
| `schattenstadt/agent.py` | Agent mit `hunger`, `energie`, `geld`, die jeden Tick weiterlaufen, dazu Wohnort, Job, Auto |
| `schattenstadt/actions.py` | Pydantic-Schema der Entscheidung `{agent_id, tick, action, target, reason, memory_ids}`, Aufzählung der gültigen Optionen, Prüfung |
| `schattenstadt/policy.py` | Interface `Policy`, `RoutinePolicy` (Standard), `LLMPolicy` (Stub mit TODO für den API-Call) |
| `schattenstadt/memory.py` | SQLite: Erinnerungen mit `tick, agent_id, typ, text, wichtigkeit`; Routine nur als Zähler |
| `schattenstadt/engine.py` | Tick-Loop, Retry und Fallback, Loop-Detektor, Invarianten |
| `schattenstadt/run.py` | Kommandozeile, Vergleichslauf und Auswertung |

Regeln im Detail:

- **Gültige Entscheidung:** `action` ist eines von `move, buy, work, rest, talk`,
  und das Paar `(action, target)` steht unter den Optionen, die die Engine
  angeboten hat. Damit ist `target` immer ein erreichbarer, offener Ort.
  `memory_ids` dürfen nur Erinnerungen nennen, die der Agent gezeigt bekam.
- **Ungültige Entscheidung:** ein Retry mit der Fehlermeldung als Rückmeldung,
  danach entscheidet die `RoutinePolicy`.
- **Loop-Detektor:** Wiederholt ein Agent dieselbe Aktionsfolge (Länge 1 bis 3)
  dreimal direkt hintereinander, ohne dass eine Aktion Hunger, Energie oder
  Geld verändert und jede Wiederholung am selben Ort beginnt, übernimmt für
  10 Ticks die `RoutinePolicy`; die Engine schreibt einen Protokolleintrag.
  Die Energie, die ein Weg kostet, zählt dabei nicht als Änderung: Ein Weg,
  der wieder am Ausgangsort endet, ist verschwendet. Gerät die `RoutinePolicy`
  selbst in einen Loop, meldet die Engine das als Fehler in den Regeln.
- **Invarianten pro Tick:** Geld nie negativ, Hunger und Energie zwischen 0
  und 100, jeder Agent in genau einer Ortsbelegung und diese passt zu seinem Ort.
- **Gedächtnis:** Die Wichtigkeit hängt nur vom Ereignistyp ab (Schließung 9,
  kritischer Hunger 8, Ladenwechsel 6, Gespräch 4, Einkauf 3). Wege, Arbeit und
  Ausruhen werden je Agent nur gezählt.

## Vereinfachungen im Prototyp

- Alle Agenten wohnen am selben Ort `wohnung`. Unterschiede entstehen durch
  Auto ja/nein, Job ja/nein und Geld.
- Ein Weg dauert immer einen Tick, egal wie lang er ist. Die Wegzeit in
  Minuten kostet Energie und geht in die Auswertung ein.
- Läden haben keine Öffnungszeiten; ein Einkauf senkt den Hunger sofort.
- Fixkosten werden nur bis zum Kontostand 0 abgebucht, Schulden gibt es nicht.
- Schließt die Wohnung (`--close wohnung`), ruhen die Agenten im Park.

## Auswertung

Bei einer Schließung rechnet `run.py` zusätzlich einen **Vergleichslauf**
mit gleichem Seed ohne Schließung. Bis zur Schließung sind beide Läufe
identisch, danach zeigt der Unterschied die Wirkung der Schließung.

- **Einkaufswegzeit:** Minuten des Hinwegs zum Laden, von dem Ort aus, an dem
  der Agent losging (meist Wohnung oder Arbeit).
- **Betroffen:** Ein Agent braucht ab der Schließung je Einkauf im Schnitt
  mindestens 1 Minute länger als im Vergleichslauf im selben Zeitraum, oder
  er kauft gar nicht mehr ein. Kleinere Unterschiede entstehen schon, wenn
  ein Agent von einem anderen Ort aus losgeht.
- **Wen trifft es am härtesten:** mittlerer Mehrweg je Einkauf gegenüber dem
  Vergleich, nach Auto und Job gruppiert.
- Zusätzlich zeigt die Ausgabe die reine Strecke Wohnung → nächster offener
  Laden vor und nach der Schließung.

## Start

Voraussetzung: Python 3.12.

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
python -m schattenstadt.run --agents 20 --ticks 100 --close supermarkt --at 50
```

Ausgabe dieses Laufs:

```
Schattenstadt: 20 Agenten (7 mit Auto), 100 Ticks, Seed 42, Policy routine
Schließung: supermarkt bei Tick 50. Vergleich: gleicher Seed ohne Schließung.

Weg von der Wohnung zum nächsten offenen Laden (Minuten)
              vorher  nachher
  mit Auto       3,0      8,0
  ohne Auto      7,0     28,0

Durchschnittliche Einkaufswegzeit (Minuten je Hinweg zum Laden)
              vorher  nachher  Vergleich   Wege vorher/nachher/Vergleich
  alle           6,8     19,6        6,9   55/60/60
  mit Auto       3,8      7,6        4,0   20/21/21
  ohne Auto      8,5     26,1        8,5   35/39/39
  Vergleich: Lauf ohne Schließung, ebenfalls ab Tick 50.

Betroffene Agenten (ab Tick 50 je Einkauf mindestens 1,0 min länger als im Vergleich, oder kein Einkauf mehr): 20 von 20
  mit Auto:   7 von 7
  ohne Auto:  13 von 13

Mehrweg je Einkauf gegenüber dem Vergleich, nach Gruppe
  ohne Auto, ohne Job    +21,3 min   6 Agenten
  ohne Auto, mit Job     +14,4 min   7 Agenten
  mit Auto, ohne Job      +5,7 min   1 Agent
  mit Auto, mit Job       +3,3 min   6 Agenten

Lauf: 2000 Entscheidungen, 0 Fehlversuche, 0 Fallbacks auf Routine, 0 Loop-Eingriffe, 317 Erinnerungen gespeichert, 1794 Routineaktionen gezählt. Invarianten in jedem Tick geprüft.
```

Zum Lesen: Dass alle 20 Agenten betroffen sind, folgt aus dem Aufbau, denn
alle wohnen am selben Ort und für alle ist der Supermarkt der nächste Laden.
Agenten mit Job trifft es weniger: Etwa ein Drittel ihrer Einkäufe startet an
der Arbeit, und von dort ist der Discounter nah (16 statt 28 Minuten zu Fuß).
Mit einer echten Karte werden diese Zahlen anders aussehen.

Weitere Optionen: `--seed` (Standard 42), `--db datei.sqlite` (Erinnerungen
des Hauptlaufs in eine Datei statt nur in den Speicher), `-v` (Protokoll:
Schließung, Loop-Eingriffe), `--policy llm` (ruft den Stub auf; weil der
API-Call fehlt, schlägt jeder Versuch fehl und jede Entscheidung fällt nach
einem Retry auf die `RoutinePolicy` zurück). Ohne `--close` gibt es keinen
Vergleichslauf, nur die mittlere Einkaufswegzeit.

## Tests

```bash
pytest
```

Abgedeckt: Aktions-Validierung, Invarianten (inklusive absichtlich
verletzter Zustände), Reproduzierbarkeit (gleicher Seed, gleiches Ergebnis,
auch in getrennten Prozessen mit anderem `PYTHONHASHSEED`), Loop-Detektor
(auch: die Routine erzeugt bei keiner Schließung einen Loop), Retry und
Fallback, Gedächtnis, Auswertung und Kommandozeile.

## Roadmap

1. **LLMPolicy anbinden:** API-Call an der TODO-Stelle in `policy.py`; Prompt
   und Prüfung sind schon da.
2. **Embeddings fürs Erinnerungs-Retrieval:** statt „wichtigste und neueste“
   die zur Lage passenden Erinnerungen abrufen.
3. **Kostenmodell pro Simulationstag:** Tokens und Kosten je Agent und Tag
   messen und begrenzen. Der Vergleichslauf verdoppelt die Aufrufe.
4. **Echte Karte aus OpenStreetMap:** echte Wohnorte, Läden und Wegzeiten
   statt fünf Knoten.
5. **Kalibrierung gegen reale Mobilitätsdaten:** Einkaufswege und
   Verkehrsmittelwahl an Erhebungen abgleichen.
