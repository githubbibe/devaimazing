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

import asyncio
import json
import shlex
from pathlib import Path
from typing import Any

from studio.config import StudioConfig
from studio.metrics import record_agent_result
from studio.routing import agent_iteration_count, max_iterations_exceeded
from studio.state import AgentResult, Phase, RunStatus, StudioState
from studio.tools.claude_code import run_claude_code
from studio.tools.filesystem import inject_skills, read_card, write_card
from studio.tools.git import commit_as_agent

_DEVAIMAZING_ROOT = Path(__file__).resolve().parents[3]
_PROMPT_PATH = _DEVAIMAZING_ROOT / "prompts" / "security.md"
_SKILLS_DIR = _DEVAIMAZING_ROOT / "skills"
_SKILL_NAMES = ["error-handling"]

# Normalisation des sévérités par outil vers une échelle commune, vérifiée
# contre les schémas JSON réels (bandit results[].issue_severity,
# semgrep results[].extra.severity) — bandit : LOW/MEDIUM/HIGH (déjà la
# même échelle) ; semgrep : INFO/WARNING/ERROR par défaut (pas de CRITICAL
# nativement dans l'un ou l'autre avec la config par défaut).
_SEVERITY_NORMALIZATION = {
    "bandit": {"LOW": "LOW", "MEDIUM": "MEDIUM", "HIGH": "HIGH"},
    "semgrep": {"INFO": "LOW", "WARNING": "MEDIUM", "ERROR": "HIGH"},
}
_SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

# Tronque le rapport SAST brut injecté dans le prompt Sonnet (évite un
# prompt démesuré sur un repo avec beaucoup de findings).
_MAX_SAST_JSON_CHARS = 20000


async def _run_sast_tool(command_template: str, target_dir: Path) -> dict[str, Any]:
    """
    Exécute un outil SAST (bandit ou semgrep) et retourne son JSON.

    bandit/semgrep sortent avec un code non nul dès qu'ils trouvent des
    findings (comportement normal, pas une erreur d'outil) : le code de
    sortie n'est donc pas utilisé pour détecter un échec. Seule une sortie
    non-JSON (crash de l'outil, mauvaise commande) est traitée comme une
    erreur.

    Raises:
        RuntimeError: Si la sortie n'est pas un JSON valide.
    """
    command = shlex.split(command_template.replace("{target_dir}", str(target_dir)))
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    try:
        return json.loads(stdout.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Sortie SAST invalide pour la commande {command_template!r} : {exc}\n"
            f"{stderr.decode('utf-8', errors='replace').strip()}"
        ) from exc


def _normalized_severities(tool_name: str, payload: dict[str, Any]) -> list[str]:
    """Extrait les sévérités normalisées des findings d'un rapport SAST."""
    mapping = _SEVERITY_NORMALIZATION.get(tool_name, {})
    severities = []
    for issue in payload.get("results", []):
        if tool_name == "bandit":
            raw = issue.get("issue_severity", "")
        elif tool_name == "semgrep":
            raw = issue.get("extra", {}).get("severity", "")
        else:
            raw = ""
        severities.append(mapping.get(raw, raw))
    return severities


async def run(state: StudioState) -> StudioState:
    """
    Point d'entrée du node Sécu.

    Args:
        state: État courant du run, avec state.current_phase=
            Phase.SECURITE. state.agent_cards["secu"] doit être renseigné.

    Returns:
        État mis à jour : un AgentResult est ajouté à state.agent_results.
        Si aucun finding normalisé n'atteint sast.fail_on_severity (voir
        config/studio.yml) : state.current_phase=Phase.AUDIT_AVAL. Sinon :
        state.status=RunStatus.WAITING_HUMAN,
        state.awaiting_human_validation=True — le rapport est produit et
        commité dans les deux cas, seule la progression automatique change
        (décision : un finding bloquant remonte à l'humain avant l'audit
        aval de l'Architecte, cohérent avec la préférence du projet pour
        les checkpoints explicites — voir ADR 0008/0010 — plutôt que de
        laisser cette décision aux seuls outils SAST, qui ne s'arrêtent pas
        en cours de scan sur une sévérité, voir _run_sast_tool). Si l'agent
        a déjà atteint agents.max_iterations tentatives pour cette phase :
        aucun appel n'est fait, state.status=RunStatus.FAILED (voir Notes).

    Raises:
        RuntimeError: Si un outil SAST (bandit, semgrep) produit une sortie
            non-JSON (crash de l'outil), ou si l'appel Claude Code CLI
            échoue.
        TimeoutError: Si l'appel Sonnet dépasse claude_code.timeout_seconds.
        FileNotFoundError: Si la fiche de l'agent, ou l'exécutable d'un
            outil SAST, est introuvable.

    Side effects:
        - Exécute bandit et semgrep (config/studio.yml section sast) sur
          config.repo_path, zéro token.
        - Appelle tools.claude_code.run_claude_code (modèle
          models.agent_auditor) avec le rapport SAST brut en entrée du
          prompt (voir prompts/security.md, section Périmètre — Input) ;
          Claude Code lit le code du repo lui-même (cwd=config.repo_path),
          il n'est pas réinjecté intégralement dans le prompt.
        - Écrit specs/<structure.specs_dir>/run-<state.run_id>/
          security-report.md via tools.filesystem.write_card, avec le
          contenu produit par Sonnet (voir prompts/security.md, section
          Format du rapport — Sonnet génère les deux sections, SAST et
          couche 2, à partir du rapport SAST brut fourni en entrée).
        - Commit sous l'identité security-aimazing <security@aimazing.fr>
          via tools.git.commit_as_agent.
        - Incrémente state.total_tokens_sonnet.

    Example:
        >>> state = StudioState(
        ...     run_id="run-042",
        ...     current_phase=Phase.SECURITE,
        ...     agent_sequence=["secu"],
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

        Ni bandit ni semgrep ne produisent nativement une sévérité
        "CRITICAL" en configuration par défaut (bandit : LOW/MEDIUM/HIGH ;
        semgrep : INFO/WARNING/ERROR, normalisé ici vers LOW/MEDIUM/HIGH) —
        un `sast.fail_on_severity: CRITICAL` dans la config ne serait donc
        jamais atteint avec la configuration actuelle de config/studio.yml.
        Schémas vérifiés contre une exécution réelle des deux outils
        (2026-07-10), pas seulement déduits de leur documentation.

        Si un humain reprend le run (voir cli.py resume) sans corriger les
        findings bloquants, ce node serait ré-invoqué et pourrait boucler
        indéfiniment sur les mêmes findings : agents.max_iterations
        (config/studio.yml) s'applique aussi ici, comme filet de sécurité
        (voir studio.routing.max_iterations_exceeded). Chaque tentative est
        enregistrée via studio.metrics.record_agent_result.
    """
    config = StudioConfig.from_env()
    role = state.agent_sequence[state.current_agent_index]

    if max_iterations_exceeded(state, config, role):
        max_iterations = config.get("agents", {}).get("max_iterations", 3)
        return {
            "status": RunStatus.FAILED,
            "requires_manual_intervention": True,
            "intervention_reason": (
                f"Agent {role!r} a atteint la limite de {max_iterations} itérations "
                f"en phase {state.current_phase.name} sans succès."
            ),
            "failed_agents": (
                state.failed_agents if role in state.failed_agents
                else state.failed_agents + [role]
            ),
        }

    card_path = config.repo_path / state.agent_cards[role]
    card_content = await read_card(card_path)

    sast_config = config.get("sast", {})
    sast_reports: dict[str, Any] = {}
    all_severities: list[str] = []
    if sast_config.get("enabled", False):
        for tool in sast_config.get("tools", []):
            payload = await _run_sast_tool(tool["command"], config.repo_path)
            sast_reports[tool["name"]] = payload
            all_severities += _normalized_severities(tool["name"], payload)

    fail_on_severity = sast_config.get("fail_on_severity")
    threshold_rank = _SEVERITY_RANK.get(fail_on_severity, len(_SEVERITY_RANK))
    has_blocking_findings = any(
        _SEVERITY_RANK.get(severity, -1) >= threshold_rank for severity in all_severities
    )

    system_prompt = await inject_skills(
        base_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
        skill_names=_SKILL_NAMES,
        skills_dir=_SKILLS_DIR,
    )
    sast_json = json.dumps(sast_reports, indent=2)[:_MAX_SAST_JSON_CHARS]
    prompt = (
        f"{system_prompt}\n\n---\n\n{card_content}\n\n"
        f"## Rapport SAST brut (bandit, semgrep)\n\n```json\n{sast_json}\n```"
    )

    claude_code_config = config.get("claude_code", {})
    result = await run_claude_code(
        prompt=prompt,
        model=config.models["agent_auditor"],
        cwd=config.repo_path,
        timeout_seconds=claude_code_config.get("timeout_seconds", 300),
        output_format=claude_code_config.get("output_format", "json"),
    )

    specs_dir = config.get("structure", {}).get("specs_dir", "specs/")
    report_path = config.repo_path / specs_dir / state.run_id / "security-report.md"
    await write_card(report_path, result["content"])

    report_relative = str(Path(specs_dir) / state.run_id / "security-report.md")
    await commit_as_agent(
        repo_path=config.repo_path,
        agent="security",
        message=f"docs: security report ({'blocking findings' if has_blocking_findings else 'clean'})",
        files=[report_relative],
    )

    usage = result.get("usage", {})
    iteration = agent_iteration_count(state, role) + 1
    agent_result = AgentResult(
        agent=role,
        phase=state.current_phase,
        status="success",
        output_files=[report_relative],
        iteration=iteration,
        tokens_prompt=usage.get("input_tokens", 0),
        tokens_completion=usage.get("output_tokens", 0),
        duration_ms=result.get("duration_ms", 0),
    )
    await record_agent_result(config, state, agent_result, model=config.models["agent_auditor"], claude_code_calls=1)

    updates: dict = {
        "agent_results": state.agent_results + [agent_result],
        "total_tokens_sonnet": (
            state.total_tokens_sonnet + usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        ),
    }

    if has_blocking_findings:
        updates["status"] = RunStatus.WAITING_HUMAN
        updates["awaiting_human_validation"] = True
    else:
        updates["current_phase"] = Phase.AUDIT_AVAL

    return updates
