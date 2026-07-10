"""
Node LangGraph - Agent PM.

Seul agent stateful du studio (mémoire portée par le checkpointer SQLite
LangGraph, voir ADR 0001 et ADR 0003). Ce node couvre deux activations
distinctes du même agent, distinguées par state.current_phase :

- Phase.RECEPTION / Phase.CADRAGE (phases 0-1, modèle models.pm_opus) :
  dialogue de raffinement itératif avec l'utilisateur jusqu'à validation
  de la fiche racine (voir docs/workflow.md phase 1, checklist d'intention,
  ADR 0008).
- Phase.FICHES (phase 3, modèle models.pm_sonnet) : définition de la
  séquence d'agents et écriture d'une fiche par agent, puis création de
  la branche du run (premier commit-point, voir ADR 0007).

Le node ne couvre pas la phase 10 (clôture) : celle-ci est gérée par
studio.nodes.closer, Python pur sans appel LLM.
"""

from studio.config import StudioConfig
from studio.state import AgentResult, Phase, RunStatus, StudioState
from studio.tools.claude_code import run_claude_code
from studio.tools.filesystem import write_card
from studio.tools.git import create_run_branch


async def run(state: StudioState) -> StudioState:
    """
    Point d'entrée du node PM.

    Args:
        state: État courant du run. state.current_phase détermine le
            comportement (voir description du module). state.objective_raw
            doit être renseigné dès Phase.RECEPTION.

    Returns:
        État mis à jour :
        - En Phase.CADRAGE, tant que la fiche racine n'est pas validée :
          state.awaiting_human_validation=True, state.status=WAITING_HUMAN,
          state.current_phase inchangée.
        - En Phase.CADRAGE, une fois validée : state.card_root_path
          renseigné, state.current_phase=Phase.AUDIT_AMONT.
        - En Phase.FICHES, une fois les fiches écrites et la branche
          créée : state.agent_sequence et state.agent_cards renseignés,
          state.current_phase=Phase.STUBS.
        Dans tous les cas, un AgentResult est ajouté à state.agent_results.

    Raises:
        RuntimeError: Si l'appel à Claude Code CLI échoue (voir
            tools/claude_code.py::run_claude_code).
        TimeoutError: Si l'appel dépasse claude_code.timeout_seconds
            (config/studio.yml).
        ValueError: Si state.objective_raw est vide en Phase.RECEPTION.

    Side effects:
        - Appelle tools.claude_code.run_claude_code (modèle models.pm_opus
          en Phase.CADRAGE, models.pm_sonnet en Phase.FICHES).
        - Écrit specs/run-NNN/card-root.md (phase 1) et une fiche par agent
          dans specs/run-NNN/ (phase 3) via tools.filesystem.write_card.
        - Crée la branche du run via tools.git.create_run_branch,
          uniquement à la validation de la Phase.FICHES (jamais pendant le
          dialogue de cadrage — voir docs/workflow.md phase 1).
        - Incrémente state.total_tokens_opus (Phase.CADRAGE) ou
          state.total_tokens_sonnet (Phase.FICHES).

    Example:
        >>> state = StudioState(
        ...     run_id="run-042",
        ...     project_name="webaimazing-v2",
        ...     objective_raw="ajouter un endpoint de login",
        ...     current_phase=Phase.CADRAGE,
        ... )
        >>> state = await run(state)
        >>> state.card_root_path
        'specs/run-042/card-root.md'

    Notes:
        Un trou d'intention détecté par la checklist (docs/workflow.md
        phase 1) ne doit jamais être comblé par une valeur par défaut
        « raisonnable » — il positionne awaiting_human_validation=True et
        remonte à l'utilisateur (ADR 0008, garde-fou non négociable).
        La config du run (repo_path, modèles, chemins) est chargée via
        StudioConfig.from_env(), pas transmise dans StudioState.
    """
    ...
