"""
Registre d'outils partagé (ADR 0013, Décision 4).

Point d'appel unique consommé de façon identique par le parsing des
commandes slash Telegram et par le function-calling de l'agent Devaimazing
(tranches S2/S4, pas encore implémentées) — pas de duplication de logique
entre les deux voies d'entrée. La confirmation avant exécution n'est pas une
propriété du canal d'appel, c'est une propriété de l'outil lui-même
(`ToolSpec.requiert_confirmation`).

Tranche S1 (voir docs/roadmap.md) : seuls `lire_statut`, `lire_progression`
et `lister_projets` ont un handler réel. `creer_projet`, `archive_projet`,
`reject_checkpoint` et `stop_run` sont déclarés avec leurs métadonnées
définitives (table ADR 0013, Décision 4) mais lèvent NotImplementedError —
câblés dans une tranche ultérieure.
"""

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from studio.config import StudioConfig
from studio.tools import queries


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


async def _handle_lire_statut(config: StudioConfig, *, run_id: str) -> dict[str, Any]:
    return await queries.get_run_snapshot(config, run_id)


async def _handle_lire_progression(config: StudioConfig, *, run_id: str) -> dict[str, Any]:
    return await queries.get_run_progression(config, run_id)


async def _handle_lister_projets(config: StudioConfig, **_: Any) -> dict[str, Any]:
    names = await queries.list_projects(config.config_dir)
    return {"projects": names}


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
        handler=_not_implemented,
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
        handler=_not_implemented,
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
        data = await spec.handler(config, **args)
    except NotImplementedError as exc:
        return ToolResult(status="error", summary=str(exc))

    return ToolResult(status="ok", summary=f"{spec.name} exécuté avec succès.", data=data)
