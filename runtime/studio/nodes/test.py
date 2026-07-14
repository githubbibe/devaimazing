"""
Node LangGraph - Agent Test.

Agent stateless (ADR 0001) tournant sur Qwen 2.5 7B via Ollama
(models.agents_local). Périmètre : /tests/integration/, /tests/e2e/,
lecture transverse (voir docs/agents.md). Activé en Phase.TESTS (phase 7),
après que Back, Front et leurs sous-rôles -tu ont terminé la phase 6.

Les tests unitaires (Back-tu, Front-tu) ne sont pas gérés par ce node : ils
partagent les nodes backend/frontend (voir studio.routing.AGENT_TO_NODE).
"""

import asyncio
import shlex
from pathlib import Path

from studio.config import StudioConfig
from studio.metrics import record_agent_result
from studio.routing import agent_iteration_count, max_iterations_exceeded
from studio.state import AgentResult, Phase, RunStatus, StudioState
from studio.tools.filesystem import (
    append_feedback,
    inject_skills,
    parse_structured_file_output,
    read_card,
    read_files,
    write_card,
)
from studio.tools.git import commit_as_agent
from studio.tools.ollama import FILE_OUTPUT_SCHEMA, run_ollama

_DEVAIMAZING_ROOT = Path(__file__).resolve().parents[3]
_PROMPT_PATH = _DEVAIMAZING_ROOT / "prompts" / "test.md"
_SKILLS_DIR = _DEVAIMAZING_ROOT / "skills"
_SKILL_NAMES = ["non-regression"]

# Nombre de caractères d'output de test conservés dans la fiche en cas
# d'échec (évite de dumper une sortie de suite de tests entière).
_MAX_FEEDBACK_OUTPUT_CHARS = 4000


async def _run_test_command(command_template: str, target_dir: Path) -> tuple[bool, str]:
    """
    Exécute la commande de test du projet cible (voir config.test_command).

    Args:
        command_template: Commande avec placeholder {target_dir}.
        target_dir: Répertoire du projet cible, substitué dans le template.

    Returns:
        Tuple (succès, sortie combinée stdout+stderr). succès = code de
        sortie 0.
    """
    command = shlex.split(command_template.replace("{target_dir}", str(target_dir)))
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(target_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    output = (stdout + stderr).decode("utf-8", errors="replace")
    return process.returncode == 0, output


async def run(state: StudioState) -> StudioState:
    """
    Point d'entrée du node Test.

    Args:
        state: État courant du run, avec state.current_phase=Phase.TESTS.
            state.agent_cards["test"] doit référencer les zones d'impact
            identifiées par l'Architecte en phase 2 (architect-brief.md).

    Returns:
        État mis à jour :
        - Si l'agent signale un blocage (champ "blocked_reason" non vide
          dans sa sortie structurée — voir tools.ollama.FILE_OUTPUT_SCHEMA),
          ou si "files" est vide : feedback ajouté à sa propre fiche,
          AgentResult.status="feedback_sent",
          state.status=RunStatus.WAITING_HUMAN.
        - Si config.test_command est défini (voir config/projects/<nom>.yml
          section test) et que son exécution échoue (code de sortie non
          nul) : traité comme un échec de non-régression — feedback ajouté
          à la fiche avec l'output de la commande, AgentResult.status=
          "error", state.status=RunStatus.WAITING_HUMAN. L'agent Test ne
          corrige ni le test ni le code (voir docs/workflow.md phase 7).
        - Si config.test_command n'est pas défini pour ce projet : les
          tests sont écrits et commités mais pas exécutés (voir Notes) ;
          state.current_phase avance normalement à Phase.SECURITE.
        - Sinon (commande de test définie et réussie) :
          state.current_phase=Phase.SECURITE.
        Un AgentResult est ajouté à state.agent_results dans tous les cas.

    Raises:
        RuntimeError: Si l'appel Ollama échoue après agents.max_iterations
            tentatives.
        TimeoutError: Si l'appel dépasse ollama.timeout_seconds.
        FileNotFoundError: Si la fiche de l'agent, ou l'exécutable de la
            commande de test, est introuvable.

    Side effects:
        - Appelle tools.ollama.run_ollama (modèle models.agents_local), avec
          response_format=tools.ollama.FILE_OUTPUT_SCHEMA (sortie contrainte
          par grammaire — voir docs/roadmap.md, chantier "sortie structurée",
          2026-07-11).
        - Crée des fichiers dans /tests/integration/ et /tests/e2e/ (les
          chemins exacts renvoyés par l'agent, sans validation de périmètre
          — rôle de l'Architecte en phase 9).
        - Si config.test_command est défini : exécute cette commande dans
          config.repo_path.
        - Commit sous l'identité test-aimazing <test@aimazing.fr> via
          tools.git.commit_as_agent.
        - Incrémente state.total_tokens_ollama.

    Example:
        >>> state = StudioState(
        ...     run_id="run-042",
        ...     current_phase=Phase.TESTS,
        ...     agent_sequence=["test"],
        ...     agent_cards={"test": "specs/run-042/test.md"},
        ... )
        >>> state = await run(state)
        >>> state.current_phase
        <Phase.SECURITE: 8>

    Notes:
        Décision prise avec l'utilisateur (2026-07-10) : la commande de
        test est définie par projet (config/projects/<nom>.yml, section
        test), pas globalement — les stacks cibles sont hétérogènes, et
        ça permet de tester le SI dans un environnement de développement
        distinct de celui de devaimazing. Aucune commande par défaut :
        un projet sans section `test` fait écrire les tests sans les
        exécuter (dégradé, mais pas bloquant).

        La notification ntfy "❌ [Test] non-régression échouée" mentionnée
        dans docs/workflow.md n'est pas encore câblée (pas d'outil de
        notification implémenté, topic ntfy toujours à
        <PLACEHOLDER_TOPIC> — voir docs/roadmap.md).

        Le run s'arrête sur un échec de non-régression, sans retry
        automatique — la correction implique potentiellement Back ou
        Front, hors périmètre de l'agent Test (docs/agents.md, Règles de
        périmètre).

        Si l'agent a déjà atteint agents.max_iterations tentatives pour
        cette phase (voir studio.routing.max_iterations_exceeded) : aucun
        appel Ollama n'est fait, state.status=RunStatus.FAILED (voir
        Returns). Chaque tentative est enregistrée via
        studio.metrics.record_agent_result.
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
    existing_files_context = await read_files(
        config.repo_path, state.agent_card_metadata[role]["existing_files_to_read"]
    )
    user_prompt = (
        f"{existing_files_context}\n\n---\n\n{card_content}" if existing_files_context else card_content
    )

    system_prompt = await inject_skills(
        base_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
        skill_names=_SKILL_NAMES,
        skills_dir=_SKILLS_DIR,
    )

    ollama_config = config.get("ollama", {})
    result = await run_ollama(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=config.models["agents_local"],
        base_url=config.ollama_base_url,
        timeout_seconds=ollama_config.get("timeout_seconds", 120),
        response_format=FILE_OUTPUT_SCHEMA,
    )
    tokens_used = result["tokens_prompt"] + result["tokens_completion"]

    iteration = agent_iteration_count(state, role) + 1

    try:
        files, blocked_reason = parse_structured_file_output(result["content"])
    except ValueError:
        files, blocked_reason = {}, ""

    if blocked_reason or not files:
        await append_feedback(
            card_path, agent_source=role, feedback=blocked_reason or result["content"]
        )
        agent_result = AgentResult(
            agent=role,
            phase=state.current_phase,
            status="feedback_sent",
            iteration=iteration,
            tokens_prompt=result["tokens_prompt"],
            tokens_completion=result["tokens_completion"],
            duration_ms=result["duration_ms"],
        )
        await record_agent_result(config, state, agent_result, model=config.models["agents_local"])
        return {
            "agent_results": state.agent_results + [agent_result],
            "status": RunStatus.WAITING_HUMAN,
            "awaiting_human_validation": True,
            "total_tokens_ollama": state.total_tokens_ollama + tokens_used,
        }

    for relative_path, content in files.items():
        await write_card(config.repo_path / relative_path, content)

    await commit_as_agent(
        repo_path=config.repo_path,
        agent="test",
        message=f"test: integration/non-regression - {', '.join(sorted(files))}",
        files=sorted(files.keys()),
    )

    result_kwargs = dict(
        agent=role,
        phase=state.current_phase,
        output_files=sorted(files.keys()),
        iteration=iteration,
        tokens_prompt=result["tokens_prompt"],
        tokens_completion=result["tokens_completion"],
        duration_ms=result["duration_ms"],
    )

    test_command = config.test_command
    if test_command is not None:
        passed, output = await _run_test_command(test_command, config.repo_path)
        if not passed:
            await append_feedback(
                card_path, agent_source=role, feedback=output[-_MAX_FEEDBACK_OUTPUT_CHARS:]
            )
            agent_result = AgentResult(status="error", **result_kwargs)
            await record_agent_result(config, state, agent_result, model=config.models["agents_local"])
            return {
                "agent_results": state.agent_results + [agent_result],
                "status": RunStatus.WAITING_HUMAN,
                "awaiting_human_validation": True,
                "total_tokens_ollama": state.total_tokens_ollama + tokens_used,
            }

    agent_result = AgentResult(status="success", **result_kwargs)
    await record_agent_result(config, state, agent_result, model=config.models["agents_local"])
    return {
        "agent_results": state.agent_results + [agent_result],
        "total_tokens_ollama": state.total_tokens_ollama + tokens_used,
        "current_phase": Phase.SECURITE,
        "current_agent_index": 0,
    }
