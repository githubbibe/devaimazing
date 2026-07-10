"""
Node LangGraph - Agent Sécu.

Deux couches complémentaires, toutes deux activées en Phase.SECURITE
(phase 8) :

- Couche 1 (SAST déterministe, zéro token) : bandit et semgrep, configurés
  dans config/studio.yml section sast, lancés par le runtime avant
  l'activation du modèle.
- Couche 2 (audit Sonnet, models.agent_auditor) : audite ce que le SAST ne
  couvre pas — autorisation et logique métier, cohérence globale, gestion
  des erreurs au-delà des patterns SAST (voir prompts/security.md).

Agent stateless (ADR 0001). Périmètre : lecture transverse, écriture dans
specs/run-NNN/security-report.md (voir docs/agents.md).
"""

from studio.config import StudioConfig
from studio.state import AgentResult, Phase, StudioState
from studio.tools.claude_code import run_claude_code
from studio.tools.filesystem import write_card
from studio.tools.git import commit_as_agent


async def run(state: StudioState) -> StudioState:
    """
    Point d'entrée du node Sécu.

    Args:
        state: État courant du run, avec state.current_phase=
            Phase.SECURITE. state.agent_cards["secu"] doit être renseigné.

    Returns:
        État mis à jour : state.current_phase=Phase.AUDIT_AVAL une fois le
        rapport produit, que des findings CRITICAL/HIGH aient été relevés
        ou non (le blocage sur sévérité, sast.fail_on_severity, arrête
        l'exécution des outils SAST eux-mêmes, pas la progression du run
        au-delà de ce node — voir Raises). Un AgentResult est ajouté à
        state.agent_results.

    Raises:
        RuntimeError: Si un outil SAST (bandit, semgrep) retourne un code
            d'erreur non nul, ou si l'appel Claude Code CLI échoue. Message
            ntfy correspondant au premier cas : "❌ SAST échec — <constat
            brut>" (voir docs/workflow.md, section Notifications).
        TimeoutError: Si l'appel Sonnet dépasse claude_code.timeout_seconds.

    Side effects:
        - Exécute bandit et semgrep (config/studio.yml section sast) sur le
          code produit par Back/Front, zéro token.
        - Appelle tools.claude_code.run_claude_code (modèle
          models.agent_auditor) avec le rapport SAST en entrée du prompt
          (voir prompts/security.md, section Périmètre — Input).
        - Écrit specs/run-NNN/security-report.md via
          tools.filesystem.write_card, avec deux sections distinctes
          (Findings SAST, Findings couche 2 — voir prompts/security.md,
          section Format du rapport).
        - Commit sous l'identité security-aimazing <security@aimazing.fr>
          via tools.git.commit_as_agent.
        - Incrémente state.total_tokens_sonnet.

    Example:
        >>> state = StudioState(
        ...     run_id="run-042",
        ...     current_phase=Phase.SECURITE,
        ...     agent_cards={"secu": "specs/run-042/secu.md"},
        ... )
        >>> state = await run(state)
        >>> state.current_phase
        <Phase.AUDIT_AVAL: 9>

    Notes:
        La couche 2 ne ré-audite jamais le territoire déjà couvert par le
        SAST (injections, secrets, patterns connus de bandit/semgrep) —
        elle se concentre sur ce qu'un outil déterministe ne peut pas
        évaluer (voir prompts/security.md).
    """
    ...
