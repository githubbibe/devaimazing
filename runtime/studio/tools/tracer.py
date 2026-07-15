"""
Trace d'exécution structurée par run (JSON Lines).

Comble le vide entre les métriques quantitatives (studio.metrics — tokens,
durée, itérations) et l'affichage terminal éphémère (console.print des
nodes, perdu après le run) : quand un run échoue en phase N, rien ne
permettait jusqu'ici de remonter à sa cause en phase N-2 sans relancer le
run (voir docs/roadmap.md, chantier "traçabilité d'exécution", 2026-07-15).

Pas d'OpenTelemetry (surdimensionné pour un process local solo) : un simple
fichier specs/<specs_dir>/<run_id>/trace.jsonl, une ligne JSON par
événement, ouvert en mode append à chaque emit() — pas de handle de fichier
tenu ouvert entre deux appels (les nodes sont stateless et indépendants,
voir ADR 0001 ; RunTracer est reconstruit à chaque activation de node à
partir de StudioConfig + state.run_id, jamais persisté dans StudioState
lui-même pour ne pas avoir à l'exclure de la sérialisation du checkpointer
LangGraph). Commité avec le reste du run (cohérent avec ADR 0007).
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Nombre de caractères de sortie brute conservés dans un événement d'erreur
# de parsing (raw_output_head) — assez pour diagnostiquer, pas assez pour
# dupliquer une fiche/réponse entière dans la trace.
RAW_OUTPUT_HEAD_CHARS = 500


class RunTracer:
    """
    Émetteur d'événements de trace pour un run donné.

    Chaque emit() ouvre trace_path en mode append, écrit une ligne JSON,
    referme. Reconstruit à peu de frais à chaque activation de node (même
    pattern que StudioConfig.from_env() dans chaque nodes/*.py) plutôt que
    transporté dans StudioState.
    """

    def __init__(self, trace_path: Path, run_id: str):
        self.trace_path = Path(trace_path)
        self.run_id = run_id
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_run(cls, config: Any, run_id: str) -> "RunTracer":
        """
        Construit le RunTracer d'un run à partir de sa config chargée.

        Args:
            config: StudioConfig du projet (repo_path + section structure).
            run_id: Identifiant du run (state.run_id).

        Returns:
            RunTracer écrivant dans
            <repo_path>/<structure.specs_dir>/<run_id>/trace.jsonl — même
            convention que les fiches d'agent (voir nodes/pm.py::_specs_dir).
        """
        specs_dir = config.get("structure", {}).get("specs_dir", "specs/")
        return cls(config.repo_path / specs_dir / run_id / "trace.jsonl", run_id)

    def emit(self, event: str, *, agent: Optional[str] = None, phase: Optional[str] = None, **fields: Any) -> None:
        """
        Ajoute une ligne JSON à trace_path.

        Args:
            event: Type d'événement (ex: llm_call_start, llm_call_end,
                card_written, card_read, referenced_files_resolved,
                parse_output, commit, node_enter, node_exit, retry,
                warning, error).
            agent: Rôle de l'agent concerné (ex: "back-tu"), None pour un
                événement au niveau du run entier (voir run_start/run_end).
            phase: Nom de la phase courante (state.current_phase.name),
                None si non applicable.
            **fields: Champs libres selon `event` (model, tokens_prompt,
                tokens_completion, duration_ms, path, requested, found,
                missing, raw_output_head, etc.).

        Side effects:
            Ouvre trace_path en mode append, écrit une ligne, referme.
            Crée les répertoires parents s'ils n'existent pas encore.
        """
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "agent": agent,
            "phase": phase,
            "event": event,
            **fields,
        }
        with self.trace_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def for_agent(self, agent: str, phase: Any) -> "AgentTracer":
        """
        Vue de ce RunTracer pré-remplie d'agent/phase, passée aux tools
        (claude_code, ollama, git, filesystem) pour qu'ils n'aient pas à
        connaître le contexte du node appelant.

        Args:
            agent: Rôle de l'agent courant (ex: "back-tu").
            phase: state.current_phase (Phase) ou déjà une chaîne — le nom
                (.name) est utilisé si disponible, sinon la valeur telle
                quelle.
        """
        phase_label = phase.name if hasattr(phase, "name") else phase
        return AgentTracer(self, agent, phase_label)


class AgentTracer:
    """Vue de RunTracer liée à un (agent, phase) donné — voir RunTracer.for_agent."""

    def __init__(self, tracer: RunTracer, agent: str, phase: Optional[str]):
        self._tracer = tracer
        self.agent = agent
        self.phase = phase

    def emit(self, event: str, **fields: Any) -> None:
        self._tracer.emit(event, agent=self.agent, phase=self.phase, **fields)
