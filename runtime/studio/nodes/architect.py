"""
Node LangGraph - Agent Architecte.

Agent stateless (ADR 0001) tournant sur Sonnet (models.agent_auditor, voir
docs/llm-strategy.md — principe auditeur/producteur, ARCHITECTURE.md
principe 4). Ce node couvre trois activations distinctes selon
state.current_phase :

- Phase.AUDIT_AMONT (phase 2) : produit le brief architectural
  (specs/run-NNN/architect-brief.md) à partir de card-root.md et des
  cartes projet (project-map.md, architect-map.md).
- Phase.AUDIT_STUBS (phase 5) : audite les stubs produits par Back/Front
  (phase 4), annote la fiche de l'agent fautif si écart détecté.
- Phase.AUDIT_AVAL (phase 9) : audite la conformité finale, produit la
  documentation complète (ADR, OpenAPI, README, CHANGELOG, runbooks) et
  détecte la factorisation à planifier (sans la faire).
"""

from studio.config import StudioConfig
from studio.state import AgentResult, Phase, StudioState
from studio.tools.claude_code import run_claude_code
from studio.tools.filesystem import append_feedback, read_card, write_card


async def run(state: StudioState) -> StudioState:
    """
    Point d'entrée du node Architecte.

    Args:
        state: État courant du run. state.current_phase détermine le
            comportement (voir description du module). state.card_root_path
            et state.agent_cards doivent être renseignés selon la phase.

    Returns:
        État mis à jour :
        - En Phase.AUDIT_AMONT : state.architect_brief_path renseigné,
          state.current_phase=Phase.FICHES.
        - En Phase.AUDIT_STUBS, si conforme : state.current_phase=
          Phase.IMPLEMENTATION. Si écart détecté : state.awaiting_human_
          validation=True (selon config.checkpoints) et l'agent fautif est
          ajouté à state.failed_agents pour renvoi ; state.current_phase
          inchangée.
        - En Phase.AUDIT_AVAL : documentation écrite, state.current_phase=
          Phase.CLOTURE.
        Dans tous les cas, un AgentResult est ajouté à state.agent_results.

    Raises:
        RuntimeError: Si l'appel Claude Code CLI échoue.
        TimeoutError: Si l'appel dépasse claude_code.timeout_seconds.
        FileNotFoundError: Si une fiche ou une carte projet attendue est
            introuvable (card-root.md, project-map.md, architect-map.md).

    Side effects:
        - Appelle tools.claude_code.run_claude_code (modèle
          models.agent_auditor).
        - Écrit specs/run-NNN/architect-brief.md (phase 2) ou la
          documentation dans docs/ (phase 9) via tools.filesystem.write_card.
        - En cas d'écart détecté (phase 5) : annote la fiche de l'agent
          fautif via tools.filesystem.append_feedback.
        - Incrémente state.total_tokens_sonnet.
        - Ne modifie jamais de fichier hors de son périmètre déclaré
          (docs/, specs/run-NNN/architect-*.md — voir docs/agents.md,
          section Règles de périmètre).

    Example:
        >>> state = StudioState(
        ...     run_id="run-042",
        ...     current_phase=Phase.AUDIT_STUBS,
        ...     agent_cards={"back": "specs/run-042/back.md"},
        ... )
        >>> state = await run(state)
        >>> state.current_phase
        <Phase.IMPLEMENTATION: 6>

    Notes:
        L'Architecte compare systématiquement les diffs produits avec les
        périmètres déclarés dans les fiches (docs/agents.md). Un agent qui
        modifie un fichier hors périmètre voit sa contribution rejetée, pas
        seulement annotée.
    """
    ...
