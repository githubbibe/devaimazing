"""
Registre d'outils partagé (ADR 0013, Décision 4).

Point d'appel unique consommé de façon identique par le parsing des
commandes slash Telegram (tranche S2) et par le function-calling de l'agent
Devaimazing (tranche S4, pas encore implémentée) — pas de duplication de
logique entre les deux voies d'entrée. La confirmation avant exécution n'est
pas une propriété du canal d'appel, c'est une propriété de l'outil lui-même
(`ToolSpec.requiert_confirmation`).

`reject_checkpoint` et `stop_run` restent NotImplementedError (tranche S3) :
ils demandent une décision de conception séparée (aucun mécanisme
d'annulation de run ni de rejet de checkpoint n'existe dans le runtime
actuel — `resume` n'implémente que l'acceptation), pas juste un handler à
écrire.

`bot`/`chat_id` (execute_tool) : contexte Telegram optionnel, transmis aux
handlers qui en ont besoin (creer_projet, archive_projet — appellent l'API
Telegram directement). Typé Any plutôt qu'aiogram.Bot pour ne pas coupler ce
module, transport-agnostic en intention, à une lib de bot précise.
"""

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from studio.config import StudioConfig
from studio.tools import queries
from studio.tools.git import commit_safety_snapshot, current_branch, push_branch
from studio.tools.project_config import set_project_thread_id


@dataclass(frozen=True)
class ToolSpec:
    """
    Déclaration d'un outil du registre.

    Args:
        name: Identifiant unique de l'outil (utilisé aussi comme nom de
            fonction pour le function-calling Ollama, voir to_ollama_tool).
        description: Phrase en français, réutilisée telle quelle comme
            description function-calling.
        parameters: JSON Schema des arguments attendus
            ({"type": "object", "properties": {...}, "required": [...]}).
        destructif: L'action est-elle destructrice ou irréversible dans ses
            conséquences (voir ADR 0013, Décision 4).
        requiert_confirmation: Généralement égal à destructif, laissé comme
            propriété distincte au cas où un outil non destructeur
            mériterait quand même une confirmation.
        sauvegarde_avant: Si vrai, une sauvegarde (commit + push) doit être
            faite avant que l'action ne s'exécute.
        handler: Fonction async exécutant l'outil, appelée avec `config` et
            les arguments de `parameters`. Retourne un dict de données brut
            (mis dans ToolResult.data par execute_tool).
        slash_command: Commande slash Telegram associée (ex. "/status"),
            None si l'outil n'est pas exposé en commande directe.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    destructif: bool
    requiert_confirmation: bool
    sauvegarde_avant: bool
    handler: Callable[..., Awaitable[dict[str, Any]]]
    slash_command: Optional[str] = None


@dataclass(frozen=True)
class ToolResult:
    """
    Résultat d'exécution d'un outil via execute_tool.

    Args:
        status: "ok" (handler exécuté avec succès), "needs_confirmation"
            (requiert_confirmation=True et confirmed=False, handler pas
            exécuté), ou "error" (handler non implémenté ou en échec).
        summary: Résumé en français, affichable tel quel (statut actuel
            pour needs_confirmation, message d'erreur pour error).
        data: Données brutes retournées par le handler (vide sauf status="ok").
    """

    status: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)


def _no_args_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "required": []}


async def _handle_lire_statut(config: StudioConfig, *, run_id: str, **_: Any) -> dict[str, Any]:
    return await queries.get_run_snapshot(config, run_id)


async def _handle_lire_progression(
    config: StudioConfig, *, run_id: str, **_: Any
) -> dict[str, Any]:
    return await queries.get_run_progression(config, run_id)


async def _handle_lister_projets(config: StudioConfig, **_: Any) -> dict[str, Any]:
    names = await queries.list_projects(config.config_dir)
    return {"projects": names}


async def _handle_creer_projet(
    config: StudioConfig, *, name: str, bot: Optional[Any] = None,
    chat_id: Optional[int] = None, **_: Any,
) -> dict[str, Any]:
    """
    Crée le topic Telegram d'un projet déjà initialisé (`devaimazing
    new-project`) et enregistre son thread_id (voir ADR 0013, Décision 3).
    """
    if bot is None or chat_id is None:
        raise RuntimeError("creer_projet nécessite un contexte Telegram (bot, chat_id).")

    project_yml = config.config_dir / "projects" / f"{name}.yml"
    if not project_yml.is_file():
        raise ValueError(
            f"Projet {name!r} inconnu — créez-le d'abord avec "
            f"`devaimazing new-project {name}` avant de lui associer un topic."
        )

    topic = await bot.create_forum_topic(chat_id=chat_id, name=name)
    await set_project_thread_id(project_yml, thread_id=topic.message_thread_id)
    return {"project": name, "thread_id": topic.message_thread_id}


async def _handle_archive_projet(
    config: StudioConfig, *, name: str, bot: Optional[Any] = None,
    chat_id: Optional[int] = None, **_: Any,
) -> dict[str, Any]:
    """
    Archive (ferme, réversible) le topic Telegram d'un projet — sauvegarde
    (commit + push) préalable des changements non commités du repo cible,
    sous l'identité système devaimazing-bot (sauvegarde_avant, voir ADR
    0013, Décision 4). Ne supprime jamais le topic ni son historique.
    """
    if bot is None or chat_id is None:
        raise RuntimeError("archive_projet nécessite un contexte Telegram (bot, chat_id).")

    project_config = StudioConfig(project_name=name, config_dir=config.config_dir)
    thread_id = project_config.get("telegram", {}).get("thread_id")
    if thread_id is None:
        raise ValueError(f"Projet {name!r} n'a pas de topic Telegram associé (thread_id manquant).")

    repo_path = project_config.repo_path
    commit_hash = await commit_safety_snapshot(
        repo_path, message="chore: sauvegarde avant archivage du projet (Devaimazing)",
    )
    if commit_hash is not None:
        branch = await current_branch(repo_path)
        await push_branch(repo_path, branch)

    await bot.close_forum_topic(chat_id=chat_id, message_thread_id=int(thread_id))
    return {"project": name, "commit": commit_hash, "thread_id": thread_id}


async def _not_implemented(_config: StudioConfig, **_: Any) -> dict[str, Any]:
    raise NotImplementedError("Cet outil n'est pas encore câblé (voir docs/roadmap.md).")


TOOL_REGISTRY: dict[str, ToolSpec] = {
    "lire_statut": ToolSpec(
        name="lire_statut",
        description="Lit le statut actuel d'un run (phase, agent courant, dernier résultat).",
        parameters={
            "type": "object",
            "properties": {"run_id": {"type": "string", "description": "Identifiant du run"}},
            "required": ["run_id"],
        },
        destructif=False,
        requiert_confirmation=False,
        sauvegarde_avant=False,
        handler=_handle_lire_statut,
        slash_command="/status",
    ),
    "lire_progression": ToolSpec(
        name="lire_progression",
        description=(
            "Lit la progression détaillée d'un run (diagnostic, intervention "
            "manuelle éventuelle)."
        ),
        parameters={
            "type": "object",
            "properties": {"run_id": {"type": "string", "description": "Identifiant du run"}},
            "required": ["run_id"],
        },
        destructif=False,
        requiert_confirmation=False,
        sauvegarde_avant=False,
        handler=_handle_lire_progression,
        slash_command="/progression",
    ),
    "lister_projets": ToolSpec(
        name="lister_projets",
        description="Liste les projets configurés dans config/projects/.",
        parameters=_no_args_schema(),
        destructif=False,
        requiert_confirmation=False,
        sauvegarde_avant=False,
        handler=_handle_lister_projets,
        slash_command="/projects",
    ),
    "creer_projet": ToolSpec(
        name="creer_projet",
        description="Crée un nouveau topic-projet Telegram et l'enregistre dans sa config.",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Nom du projet"}},
            "required": ["name"],
        },
        destructif=False,
        requiert_confirmation=False,
        sauvegarde_avant=False,
        handler=_handle_creer_projet,
        slash_command="/new",
    ),
    "archive_projet": ToolSpec(
        name="archive_projet",
        description="Archive (ferme, réversible) le topic-projet Telegram d'un projet.",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Nom du projet"}},
            "required": ["name"],
        },
        destructif=True,
        requiert_confirmation=True,
        sauvegarde_avant=True,
        handler=_handle_archive_projet,
        slash_command="/archive",
    ),
    "reject_checkpoint": ToolSpec(
        name="reject_checkpoint",
        description="Rejette le checkpoint de validation humaine en attente d'un run.",
        parameters={
            "type": "object",
            "properties": {"run_id": {"type": "string", "description": "Identifiant du run"}},
            "required": ["run_id"],
        },
        destructif=True,
        requiert_confirmation=True,
        sauvegarde_avant=True,
        handler=_not_implemented,
        slash_command="/reject",
    ),
    "stop_run": ToolSpec(
        name="stop_run",
        description="Arrête un run en cours.",
        parameters={
            "type": "object",
            "properties": {"run_id": {"type": "string", "description": "Identifiant du run"}},
            "required": ["run_id"],
        },
        destructif=True,
        requiert_confirmation=True,
        sauvegarde_avant=True,
        handler=_not_implemented,
        slash_command="/stop",
    ),
}

SLASH_COMMAND_TO_TOOL: dict[str, str] = {
    spec.slash_command: name for name, spec in TOOL_REGISTRY.items() if spec.slash_command
}


def to_ollama_tool(spec: ToolSpec) -> dict[str, Any]:
    """
    Traduit un ToolSpec au format attendu par ollama.AsyncClient.chat(tools=...)
    (function-calling, tranche S4 — non consommé avant).

    Args:
        spec: Déclaration d'outil à traduire.

    Returns:
        {"type": "function", "function": {"name", "description", "parameters"}}
    """
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def parse_slash_command(text: str) -> Optional[tuple[str, dict[str, Any]]]:
    """
    Reconnaît une commande slash Telegram et l'associe à un outil du registre.

    Args:
        text: Texte brut du message Telegram (ex. "/status run-042").

    Returns:
        (nom_outil, args) si `text` commence par une commande slash connue,
        None sinon (ni commande slash, ni commande reconnue).

    Notes:
        Le premier mot après la commande est traité comme `run_id` si
        l'outil correspondant en attend un — mapping minimal pour S1, à
        étoffer en S2 avec la résolution de run/projet depuis le topic.
    """
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts = text.split()
    command, rest = parts[0], parts[1:]
    tool_name = SLASH_COMMAND_TO_TOOL.get(command)
    if tool_name is None:
        return None

    spec = TOOL_REGISTRY[tool_name]
    args: dict[str, Any] = {}
    required = spec.parameters.get("required", [])
    if required and rest:
        args[required[0]] = rest[0]
    return tool_name, args


async def execute_tool(
    name: str,
    args: dict[str, Any],
    *,
    config: StudioConfig,
    confirmed: bool = False,
    bot: Optional[Any] = None,
    chat_id: Optional[int] = None,
) -> ToolResult:
    """
    Point d'appel unique du registre — identique que l'appel vienne du
    parsing d'une commande slash ou du function-calling de Devaimazing.

    Args:
        name: Nom de l'outil (clé de TOOL_REGISTRY).
        args: Arguments de l'outil (voir ToolSpec.parameters).
        config: Configuration du projet concerné.
        confirmed: True si l'utilisateur a déjà confirmé l'action (ignoré
            si l'outil ne requiert pas de confirmation).
        bot: Contexte Telegram optionnel, transmis aux handlers qui en ont
            besoin (creer_projet, archive_projet). None pour les outils qui
            n'appellent pas l'API Telegram.
        chat_id: chat_id du groupe Telegram, transmis avec bot.

    Returns:
        ToolResult — voir sa docstring pour la sémantique des statuts.

    Notes:
        Si spec.requiert_confirmation et pas confirmed : le handler n'est
        jamais appelé, un ToolResult "needs_confirmation" est retourné pour
        que le canal appelant affiche la question et rappelle execute_tool
        avec confirmed=True après accord explicite de l'utilisateur — voir
        ADR 0013, Décision 4.
    """
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        return ToolResult(status="error", summary=f"Outil inconnu : {name!r}")

    missing = [key for key in spec.parameters.get("required", []) if key not in args]
    if missing:
        return ToolResult(
            status="error",
            summary=f"Argument(s) manquant(s) pour {spec.name!r} : {', '.join(missing)}",
        )

    if spec.requiert_confirmation and not confirmed:
        return ToolResult(
            status="needs_confirmation",
            summary=f"Confirmer l'exécution de {spec.name!r} ({args}) ?",
        )

    try:
        data = await spec.handler(config, bot=bot, chat_id=chat_id, **args)
    except NotImplementedError as exc:
        return ToolResult(status="error", summary=str(exc))
    except (ValueError, RuntimeError) as exc:
        return ToolResult(status="error", summary=str(exc))

    return ToolResult(status="ok", summary=f"{spec.name} exécuté avec succès.", data=data)


def format_tool_result(result: ToolResult) -> str:
    """
    Formate un ToolResult en texte affichable tel quel (Telegram ou autre) —
    partagé entre le dispatch des commandes slash (telegram.handlers) et le
    function-calling en langage naturel (devaimazing.agent), pour ne pas
    dupliquer cette mise en forme entre les deux voies d'entrée du même
    registre (voir docstring de ce module).
    """
    if result.status == "error":
        return result.summary
    if not result.data:
        return result.summary
    return "\n".join(f"{key} : {value}" for key, value in result.data.items())
