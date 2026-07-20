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
from typing import Optional

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
from studio.tools.pyenv import extract_traceback_files
from studio.tools.tracer import RunTracer

_DEVAIMAZING_ROOT = Path(__file__).resolve().parents[3]
_PROMPT_PATH = _DEVAIMAZING_ROOT / "prompts" / "test.md"
_SKILLS_DIR = _DEVAIMAZING_ROOT / "skills"
_SKILL_NAMES = ["non-regression"]

# Nombre de caractères d'output de test conservés dans la fiche en cas
# d'échec (évite de dumper une sortie de suite de tests entière).
_MAX_FEEDBACK_OUTPUT_CHARS = 4000


def _owning_producer_agent(
    config: StudioConfig, state: StudioState, relative_path: str
) -> Optional[str]:
    """
    Détermine quel agent producteur (back ou front) est propriétaire d'un
    chemin de fichier issu d'une traceback pytest de test_command — sert à
    router un échec de non-régression vers le bon producteur (voir run(),
    Phase.TESTS) au lieu de s'arrêter systématiquement sur WAITING_HUMAN.

    Returns:
        "back"/"front" si le chemin est sous structure.backend_dir/
        frontend_dir (et que l'agent correspondant est actif dans
        state.agent_sequence pour ce run) — None si le chemin est sous
        structure.tests_dir (fichier de test lui-même : Test ne se corrige
        jamais lui-même, voir docstring de run()) ou si aucun agent
        producteur ne peut être déterminé avec confiance (préfère ne rien
        attribuer plutôt que deviner, même logique que
        architect._extract_feedback_files).
    """
    structure = config.get("structure", {})
    tests_dir = structure.get("tests_dir", "tests/")
    if tests_dir and relative_path.startswith(tests_dir):
        return None

    frontend_dir = structure.get("frontend_dir", "frontend/")
    if frontend_dir and relative_path.startswith(frontend_dir):
        return "front" if "front" in state.agent_sequence else None

    backend_dir = structure.get("backend_dir", "backend/")
    if not backend_dir or relative_path.startswith(backend_dir):
        return "back" if "back" in state.agent_sequence else None

    return None


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
          nul) : traité comme un échec de non-régression. Si l'output
          implique un fichier du périmètre Back ou Front (voir
          _owning_producer_agent, nécessite --tb=native côté pytest) : redo
          ciblé de ce producteur — feedback ajouté à SA fiche (pas celle de
          Test), state.current_phase=Phase.IMPLEMENTATION,
          state.current_agent_index pointant sur ce producteur,
          state.retry_scope rempli (voir docs/roadmap.md, 2026-07-20).
          Sinon (bug probablement dans le test lui-même, ou traceback non
          exploitable) : feedback ajouté à la fiche de Test,
          AgentResult.status="error", state.status=RunStatus.WAITING_HUMAN —
          l'agent Test ne corrige jamais lui-même ni le test ni le code
          (voir docs/workflow.md phase 7).
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
          config.repo_path, AVANT le commit (voir Notes) — pas après.
        - Commit sous l'identité test-aimazing <test@aimazing.fr> via
          tools.git.commit_as_agent, uniquement si config.test_command est
          absent ou réussit — jamais pour un test qui échoue à l'exécution.
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

        Commit déplacé après test_command (2026-07-20, voir docs/roadmap.md) :
        auparavant le commit avait lieu avant l'exécution de test_command, donc
        un test qui échouait à l'exécution finissait quand même committé —
        découvert seulement après coup, au moment de l'échec. Les fichiers de
        test sont toujours écrits sur disque avant l'exécution (test_command
        en a besoin), seul le commit Git attend le résultat.

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
    tracer = RunTracer.for_run(config, state.run_id).for_agent(role, state.current_phase)
    tracer.emit("node_enter", card=state.agent_cards.get(role))

    if max_iterations_exceeded(state, config, role):
        max_iterations = config.get("agents", {}).get("max_iterations", 3)
        tracer.emit("node_exit", status="failed", reason="max_iterations_exceeded")
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
    card_content = await read_card(card_path, tracer=tracer)
    existing_files_context = await read_files(
        config.repo_path, state.agent_card_metadata[role]["existing_files_to_read"], tracer=tracer
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
        num_ctx=ollama_config.get("num_ctx", 16384),
        response_format=FILE_OUTPUT_SCHEMA,
        tracer=tracer,
    )
    tokens_used = result["tokens_prompt"] + result["tokens_completion"]

    iteration = agent_iteration_count(state, role) + 1

    try:
        files, blocked_reason = parse_structured_file_output(result["content"], tracer=tracer)
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
        tracer.emit("node_exit", status="feedback_sent")
        return {
            "agent_results": state.agent_results + [agent_result],
            "status": RunStatus.WAITING_HUMAN,
            "awaiting_human_validation": True,
            "total_tokens_ollama": state.total_tokens_ollama + tokens_used,
        }

    for relative_path, content in files.items():
        await write_card(config.repo_path / relative_path, content, tracer=tracer)

    result_kwargs = dict(
        agent=role,
        phase=state.current_phase,
        output_files=sorted(files.keys()),
        iteration=iteration,
        tokens_prompt=result["tokens_prompt"],
        tokens_completion=result["tokens_completion"],
        duration_ms=result["duration_ms"],
    )

    # Le commit n'a lieu qu'après un test_command réussi (ou absent) — pas
    # avant (voir docs/roadmap.md, 2026-07-20) : committer avant d'avoir
    # exécuté le test laissait un commit "test: ..." en historique alors que
    # le test qu'il contient ne tourne pas encore, découvert seulement après
    # coup au moment de l'échec.
    test_command = config.test_command
    if test_command is not None:
        passed, output = await _run_test_command(test_command, config.repo_path)
        if not passed:
            agent_result = AgentResult(status="error", **result_kwargs)
            await record_agent_result(config, state, agent_result, model=config.models["agents_local"])
            feedback_text = output[-_MAX_FEEDBACK_OUTPUT_CHARS:]
            updates: dict = {
                "agent_results": state.agent_results + [agent_result],
                "total_tokens_ollama": state.total_tokens_ollama + tokens_used,
            }

            # Redo ciblé du producteur fautif si la traceback l'implique
            # (voir _owning_producer_agent) — même mécanisme que
            # architect._run_audit_stubs, mais désigné par les fichiers
            # présents dans la sortie de test_command (nécessite --tb=native
            # côté pytest pour produire des lignes File "..." exploitables,
            # voir docs/roadmap.md, 2026-07-20) plutôt que par une
            # désignation explicite d'un LLM. Si aucun fichier producteur
            # n'est identifiable (ex. le bug est dans le test lui-même),
            # aucune correction automatique : Test ne se corrige jamais
            # lui-même (voir docstring de run()).
            faulty_agent = None
            faulty_files: list[str] = []
            for relative_path in extract_traceback_files(output, config.repo_path):
                owner = _owning_producer_agent(config, state, relative_path)
                if owner is not None:
                    faulty_agent = owner
                    break
            if faulty_agent is not None:
                faulty_files = [
                    f for f in extract_traceback_files(output, config.repo_path)
                    if _owning_producer_agent(config, state, f) == faulty_agent
                ]
                faulty_card_path = config.repo_path / state.agent_cards[faulty_agent]
                await append_feedback(faulty_card_path, agent_source="test", feedback=feedback_text)
                updates["current_phase"] = Phase.IMPLEMENTATION
                updates["current_agent_index"] = state.agent_sequence.index(faulty_agent)
                updates["failed_agents"] = (
                    state.failed_agents if faulty_agent in state.failed_agents
                    else state.failed_agents + [faulty_agent]
                )
                updates["retry_scope"] = {
                    **state.retry_scope,
                    faulty_agent: {f: feedback_text for f in faulty_files},
                }
                tracer.emit(
                    "node_exit", status="error", reason="non_regression_failed",
                    escalated_to=faulty_agent, files=faulty_files,
                )
                return updates

            await append_feedback(card_path, agent_source=role, feedback=feedback_text)
            updates["status"] = RunStatus.WAITING_HUMAN
            updates["awaiting_human_validation"] = True
            tracer.emit("node_exit", status="error", reason="non_regression_failed")
            return updates

    await commit_as_agent(
        repo_path=config.repo_path,
        agent="test",
        message=f"test: integration/non-regression - {', '.join(sorted(files))}",
        files=sorted(files.keys()),
        tracer=tracer,
    )

    agent_result = AgentResult(status="success", **result_kwargs)
    await record_agent_result(config, state, agent_result, model=config.models["agents_local"])
    tracer.emit("node_exit", status="success", output_files=sorted(files.keys()))
    return {
        "agent_results": state.agent_results + [agent_result],
        "total_tokens_ollama": state.total_tokens_ollama + tokens_used,
        "current_phase": Phase.SECURITE,
        "current_agent_index": 0,
    }
