"""Die Welt: fünf Orte als Graph, Wegzeiten als Kantengewichte.

Jede Kante trägt zwei Gewichte in Minuten: ``zu_fuss`` für Agenten ohne Auto
(zu Fuß, Rad und Bus zusammengefasst) und ``auto`` für Agenten mit Auto.

Ein geschlossener Ort ist kein gültiges Ziel mehr. Wege, die an ihm
vorbeiführen, bleiben nutzbar: Mit dem Laden schließt nicht die Straße.

Die Welt führt außerdem die Belegung (welcher Agent ist an welchem Ort). Nur
die Engine ruft die ändernden Methoden auf.
"""

from __future__ import annotations

import networkx as nx

WOHNUNG = "wohnung"
ARBEIT = "arbeit"
SUPERMARKT = "supermarkt"
DISCOUNTER = "discounter"
PARK = "park"

# Feste Reihenfolge. Über diese Tupel wird iteriert, nie über Mengen von
# Strings, deren Reihenfolge von PYTHONHASHSEED abhängt.
ORTE: tuple[str, ...] = (WOHNUNG, ARBEIT, SUPERMARKT, DISCOUNTER, PARK)

ANZEIGE: dict[str, str] = {
    WOHNUNG: "Wohnung",
    ARBEIT: "Arbeit",
    SUPERMARKT: "Supermarkt",
    DISCOUNTER: "Discounter",
    PARK: "Park",
}

# Preis eines Einkaufs in Euro je Laden.
LAEDEN: dict[str, int] = {SUPERMARKT: 12, DISCOUNTER: 9}

# Orte, an denen man sich ausruhen kann.
RASTORTE: frozenset[str] = frozenset({WOHNUNG, PARK})

# (von, nach, Minuten zu Fuß, Minuten mit Auto). Der Supermarkt liegt zentral
# und nah an der Wohnung, der Discounter am Rand, aber nah an der Arbeit.
WEGE: tuple[tuple[str, str, int, int], ...] = (
    (WOHNUNG, SUPERMARKT, 7, 3),
    (WOHNUNG, PARK, 9, 4),
    (WOHNUNG, ARBEIT, 25, 9),
    (WOHNUNG, DISCOUNTER, 28, 8),
    (SUPERMARKT, PARK, 6, 3),
    (SUPERMARKT, ARBEIT, 18, 7),
    (SUPERMARKT, DISCOUNTER, 24, 7),
    (ARBEIT, DISCOUNTER, 16, 6),
)


class Welt:
    def __init__(self) -> None:
        self.graph = nx.Graph()
        self.graph.add_nodes_from(ORTE)
        for von, nach, fuss, auto in WEGE:
            self.graph.add_edge(von, nach, zu_fuss=fuss, auto=auto)
        self._geschlossen: set[str] = set()
        self.anwesend: dict[str, set[int]] = {ort: set() for ort in ORTE}
        # Das Straßennetz ändert sich nicht, deshalb werden die kürzesten
        # Wegzeiten einmal vorab berechnet.
        self._wegzeiten = {
            gewicht: {
                von: dict(ziele)
                for von, ziele in nx.all_pairs_dijkstra_path_length(self.graph, weight=gewicht)
            }
            for gewicht in ("zu_fuss", "auto")
        }

    # --- Abfragen -------------------------------------------------------

    def ist_offen(self, ort: str) -> bool:
        return ort in self.graph and ort not in self._geschlossen

    def geschlossene_orte(self) -> tuple[str, ...]:
        return tuple(ort for ort in ORTE if ort in self._geschlossen)

    def erreichbar(self, von: str, nach: str) -> bool:
        return von in self.graph and nach in self.graph and nx.has_path(self.graph, von, nach)

    def wegzeit(self, von: str, nach: str, auto: bool) -> int:
        """Kürzeste Wegzeit in Minuten für die jeweilige Fortbewegung."""
        return self._wegzeiten["auto" if auto else "zu_fuss"][von][nach]

    def anwesende(self, ort: str) -> list[int]:
        return sorted(self.anwesend[ort])

    # --- Änderungen (nur durch die Engine) ------------------------------

    def schliesse(self, ort: str) -> None:
        if ort not in self.graph:
            raise ValueError(f"unbekannter Ort: {ort}")
        self._geschlossen.add(ort)

    def platziere(self, agent_id: int, ort: str) -> None:
        self.anwesend[ort].add(agent_id)

    def bewege(self, agent_id: int, von: str, nach: str) -> None:
        self.anwesend[von].discard(agent_id)
        self.anwesend[nach].add(agent_id)
