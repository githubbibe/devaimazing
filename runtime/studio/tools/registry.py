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
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from studio.config import StudioConfig
from studio.nodes.pm import extract_feature_name
from studio.tools import planification, queries
from studio.tools.filesystem import write_card
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
    Archive le projet — sauvegarde (commit + push) préalable des
    changements non commités du repo cible, sous l'identité système
    devaimazing-bot (sauvegarde_avant, voir ADR 0013, Décision 4), puis
    SUPPRIME le topic Telegram (pas seulement fermeture, voir ADR 0015,
    Décision 5 — comportement changé par rapport à l'implémentation
    d'origine de l'ADR 0013, `close_forum_topic`) : la liste native des
    topics du groupe reste ainsi toujours à jour (uniquement les projets
    actifs), ce qui rend une commande /projects dédiée redondante. Le
    contenu du repo (fiches, commits, branches) n'est pas touché — seule
    l'interface Telegram disparaît.
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

    await bot.delete_forum_topic(chat_id=chat_id, message_thread_id=int(thread_id))
    return {"project": name, "commit": commit_hash, "thread_id": thread_id}


async def _handle_new_feature(
    config: StudioConfig, *, bot: Optional[Any] = None, chat_id: Optional[int] = None,
    message_thread_id: Optional[int] = None, **_: Any,
) -> dict[str, Any]:
    """
    Démarre le dialogue de cadrage PM pour une nouvelle feature dans le
    topic-projet courant (ADR 0015, phase 1 d'implémentation) — délègue à
    studio.telegram.pm_dialogue.start_feature_dialogue. N'écrit rien ici :
    le dialogue peut durer plusieurs tours avant de produire une fiche (voir
    valider_fiche_feature pour l'écriture, à la confirmation finale).

    Import de pm_dialogue différé (pas en tête de module) : pm_dialogue
    importe execute_tool de ce module pour la confirmation finale de la
    fiche, un import en tête créerait un cycle.
    """
    if bot is None or chat_id is None or message_thread_id is None:
        raise RuntimeError(
            "new_feature nécessite un contexte Telegram complet (bot, chat_id, topic)."
        )

    from studio.telegram.pm_dialogue import start_feature_dialogue

    await start_feature_dialogue(bot, chat_id, message_thread_id, config)
    return {}


async def _handle_modifier_feature(
    config: StudioConfig, *, feature_name: str, bot: Optional[Any] = None,
    chat_id: Optional[int] = None, message_thread_id: Optional[int] = None, **_: Any,
) -> dict[str, Any]:
    """
    Rouvre le cadrage d'une feature déjà cadrée (menu "Modifier une
    feature", ADR 0015 Décision 7 révisée) — délègue à
    studio.telegram.pm_dialogue.start_feature_edit_dialogue, qui seed le
    dialogue avec le contenu actuel de sa fiche au lieu d'une page blanche.

    Raises:
        ValueError: feature_name absent de specs/planification.md, ou fiche
            introuvable sur disque (incohérence, ne devrait pas arriver en
            usage normal).
    """
    if bot is None or chat_id is None or message_thread_id is None:
        raise RuntimeError(
            "modifier_feature nécessite un contexte Telegram complet (bot, chat_id, topic)."
        )

    entry = await planification.find_entry(config, feature_name)
    if entry is None:
        raise ValueError(f"Feature {feature_name!r} inconnue dans planification.md.")

    specs_dir = config.get("structure", {}).get("specs_dir", "specs/")
    card_root_path = config.repo_path / specs_dir / entry.run_id / "card-root.md"
    if not card_root_path.is_file():
        raise ValueError(f"Fiche introuvable pour {feature_name!r} : {card_root_path}")
    content = card_root_path.read_text(encoding="utf-8")

    from studio.telegram.pm_dialogue import start_feature_edit_dialogue

    await start_feature_edit_dialogue(
        bot, chat_id, message_thread_id, config, feature_name, content,
    )
    return {}


async def _handle_cadrer_projet(
    config: StudioConfig, *, bot: Optional[Any] = None, chat_id: Optional[int] = None,
    message_thread_id: Optional[int] = None, **_: Any,
) -> dict[str, Any]:
    """
    (Re)démarre le dialogue de cadrage PM pour la fiche projet du topic
    courant — comble un vide de l'ADR 0015 : /new_project enchaîne création
    (dossier+repo+topic) ET cadrage en un seul flux, sans point de reprise
    si le dialogue a été interrompu (state.telegram.pm_dialogue._pending_dialogues
    jamais persisté — perdu si le bot redémarre en plein dialogue) ou si le
    projet existe déjà sans être jamais passé par ce dialogue (ex.
    `devaimazing new-project` en CLI, ou config restaurée manuellement).

    Redémarre TOUJOURS le dialogue depuis le début (aucune reprise de
    transcript à mi-parcours possible, comme pour new_feature) — si
    specs/fiche-projet.md existe déjà, elle sera simplement réécrite à la
    validation finale (pas de garde ni de confirmation supplémentaire, même
    comportement que new_feature vis-à-vis d'une fiche déjà présente).

    Import de pm_dialogue différé (voir _handle_new_feature) : pas de cycle
    réel, cohérence stylistique.
    """
    if bot is None or chat_id is None or message_thread_id is None:
        raise RuntimeError(
            "cadrer_projet nécessite un contexte Telegram complet (bot, chat_id, topic)."
        )

    from studio.telegram.pm_dialogue import start_project_dialogue

    await start_project_dialogue(bot, chat_id, message_thread_id, config, config.project_name)
    return {}


async def _handle_valider_fiche_feature(
    config: StudioConfig, *, run_id: str, content: str, **_: Any,
) -> dict[str, Any]:
    """
    Écrit card-root.md pour ce run_id — pendant final du dialogue de cadrage
    PM porté sur Telegram (studio.telegram.pm_dialogue, ADR 0015), après
    confirmation Oui/Non de l'utilisateur. Pas de slash_command : déclenché
    uniquement par pm_dialogue, jamais tapé ni sélectionné directement.

    Enregistre aussi une entrée dans specs/planification.md (statut initial
    "à faire") — c'est le seul point d'écriture qui associe un nom de
    feature (extrait de la fiche) à ce run_id, condition nécessaire pour
    que /run <nom_feature> (ADR 0015, Décision 4) puisse le retrouver.
    """
    specs_dir = config.get("structure", {}).get("specs_dir", "specs/")
    card_root_relative = str(Path(specs_dir) / run_id / "card-root.md")
    await write_card(config.repo_path / card_root_relative, content)

    feature_name = extract_feature_name(content)
    await planification.upsert_entry(
        config,
        planification.PlanificationEntry(
            feature_name=feature_name,
            statut="à faire",
            run_id=run_id,
            content_hash=planification.hash_content(content),
        ),
    )
    return {"card_root_path": card_root_relative, "feature_name": feature_name}


async def _handle_new_project(
    config: StudioConfig, *, bot: Optional[Any] = None, chat_id: Optional[int] = None, **_: Any,
) -> dict[str, Any]:
    """
    Amorce la création d'un nouveau projet (ADR 0015, phase 2 d'implémentation)
    — délègue à studio.telegram.new_project_flow.start_new_project_flow, qui
    demande le nom puis orchestre dossier/repo/topic/dialogue PM. N'écrit rien
    ici : General-scope, pas de résolution de projet (voir
    tools._GENERAL_SCOPE_TOOLS dans telegram.handlers).

    Import différé (voir _handle_new_feature) : new_project_flow importe
    execute_tool de ce module.
    """
    if bot is None or chat_id is None:
        raise RuntimeError("new_project nécessite un contexte Telegram (bot, chat_id).")

    from studio.telegram.new_project_flow import start_new_project_flow

    await start_new_project_flow(bot, chat_id, config.config_dir)
    return {}


async def _handle_valider_fiche_projet(
    config: StudioConfig, *, content: str, **_: Any,
) -> dict[str, Any]:
    """
    Écrit specs/fiche-projet.md à la racine du repo cible — pendant final du
    dialogue de cadrage projet (studio.telegram.pm_dialogue.start_project_dialogue,
    ADR 0015), après confirmation Oui/Non. Pas de run_id : une fiche projet
    n'appartient à aucun run précis, à la différence d'une fiche feature (voir
    valider_fiche_feature). Pas de slash_command : déclenché uniquement par
    pm_dialogue, jamais tapé ni sélectionné directement.
    """
    specs_dir = config.get("structure", {}).get("specs_dir", "specs/")
    fiche_projet_relative = str(Path(specs_dir) / "fiche-projet.md")
    await write_card(config.repo_path / fiche_projet_relative, content)
    return {"fiche_projet_path": fiche_projet_relative}


async def _handle_run_feature(
    config: StudioConfig, *, feature_name: str, bot: Optional[Any] = None,
    chat_id: Optional[int] = None, message_thread_id: Optional[int] = None, **_: Any,
) -> dict[str, Any]:
    """
    Lance (ou reprend) le run d'une feature déjà cadrée si sa fiche a changé
    depuis son dernier run (ADR 0015, Décision 4) — délègue à
    studio.telegram.run_flow.start_run, qui répond directement dans le
    topic (rien à faire, échec déjà connu, run déjà en cours) ou lance
    l'exécution en tâche de fond.

    Import de run_flow différé (voir _handle_new_feature) : run_flow importe
    des types de ce même écosystème telegram, pas de cycle réel mais garde
    la cohérence stylistique avec les deux autres handlers Telegram.
    """
    if bot is None or chat_id is None or message_thread_id is None:
        raise RuntimeError("run_feature nécessite un contexte Telegram complet (bot, chat_id, topic).")

    from studio.telegram.run_flow import start_run

    result = await start_run(bot, chat_id, message_thread_id, config, feature_name)
    if "error" in result:
        raise ValueError(result["error"])
    return result


async def _handle_stop_run(
    config: StudioConfig, *, message_thread_id: Optional[int] = None, **_: Any,
) -> dict[str, Any]:
    """
    Arrête immédiatement, sans confirmation, ce qui est en cours dans ce
    topic — un run lancé par /run (studio.telegram.run_flow) en priorité,
    sinon un dialogue de cadrage PM en attente (studio.telegram.pm_dialogue)
    — ADR 0015, Décision 6 : dérogation explicite à la confirmation
    systématique des outils destructifs, justifiée par l'urgence que sert
    cette commande. No-op (aucune erreur) si rien n'est en cours ici.

    Sauvegarde (commit + push) automatique du repo cible si un run était
    actif (contenu potentiellement modifié sur disque, voir
    run_flow.stop_active_run) — rien à sauvegarder pour un dialogue de
    cadrage (aucune fiche écrite avant validation explicite).

    Import différé de pm_dialogue/run_flow (voir _handle_new_feature) : pas
    de cycle réel, cohérence stylistique avec les autres handlers Telegram.
    """
    if message_thread_id is None:
        raise RuntimeError("stop_run nécessite un contexte de topic (message_thread_id).")

    from studio.telegram import pm_dialogue, run_flow

    stopped_run = await run_flow.stop_active_run(message_thread_id)
    if stopped_run is not None:
        return {
            "action": "run interrompu",
            "feature": stopped_run["feature_name"],
            "commit": stopped_run["commit"],
        }

    if pm_dialogue.cancel_dialogue(message_thread_id):
        return {"action": "dialogue de cadrage interrompu"}

    return {"action": "aucun traitement en cours dans ce topic"}


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
        description="Archive un projet (sauvegarde puis supprime son topic-projet Telegram).",
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
    "new_feature": ToolSpec(
        name="new_feature",
        description="Démarre le dialogue de cadrage PM pour une nouvelle feature de ce projet.",
        parameters=_no_args_schema(),
        destructif=False,
        requiert_confirmation=False,
        sauvegarde_avant=False,
        handler=_handle_new_feature,
        slash_command="/new_feature",
    ),
    "modifier_feature": ToolSpec(
        name="modifier_feature",
        description="Rouvre le cadrage d'une feature déjà cadrée pour la modifier.",
        parameters={
            "type": "object",
            "properties": {
                "feature_name": {"type": "string", "description": "Nom de la feature"},
            },
            "required": ["feature_name"],
        },
        destructif=False,
        requiert_confirmation=False,
        sauvegarde_avant=False,
        handler=_handle_modifier_feature,
        slash_command="/modifier_feature",
    ),
    "cadrer_projet": ToolSpec(
        name="cadrer_projet",
        description="(Re)démarre le dialogue de cadrage PM pour la fiche projet de ce topic.",
        parameters=_no_args_schema(),
        destructif=False,
        requiert_confirmation=False,
        sauvegarde_avant=False,
        handler=_handle_cadrer_projet,
        slash_command="/cadrer_projet",
    ),
    "valider_fiche_feature": ToolSpec(
        name="valider_fiche_feature",
        description="Écrit la fiche feature validée par l'utilisateur (dialogue de cadrage PM).",
        parameters={
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string", "description": "Identifiant du run (dossier specs/)",
                },
                "content": {
                    "type": "string", "description": "Contenu markdown complet de la fiche",
                },
            },
            "required": ["run_id", "content"],
        },
        destructif=False,
        requiert_confirmation=True,
        sauvegarde_avant=False,
        handler=_handle_valider_fiche_feature,
        slash_command=None,
    ),
    "new_project": ToolSpec(
        name="new_project",
        description=(
            "Amorce la création d'un nouveau projet (demande le nom, puis orchestre la suite)."
        ),
        parameters=_no_args_schema(),
        destructif=False,
        requiert_confirmation=False,
        sauvegarde_avant=False,
        handler=_handle_new_project,
        slash_command="/new_project",
    ),
    "valider_fiche_projet": ToolSpec(
        name="valider_fiche_projet",
        description="Écrit la fiche projet validée par l'utilisateur (dialogue de cadrage PM).",
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string", "description": "Contenu markdown complet de la fiche",
                },
            },
            "required": ["content"],
        },
        destructif=False,
        requiert_confirmation=True,
        sauvegarde_avant=False,
        handler=_handle_valider_fiche_projet,
        slash_command=None,
    ),
    "run_feature": ToolSpec(
        name="run_feature",
        description="Lance (ou reprend) le run d'une feature déjà cadrée, si sa fiche a changé.",
        parameters={
            "type": "object",
            "properties": {
                "feature_name": {"type": "string", "description": "Nom de la feature"},
            },
            "required": ["feature_name"],
        },
        destructif=False,
        requiert_confirmation=False,
        sauvegarde_avant=False,
        handler=_handle_run_feature,
        slash_command="/run",
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
        description="Arrête immédiatement (sans confirmation) le dialogue ou run en cours ici.",
        parameters=_no_args_schema(),
        destructif=True,
        requiert_confirmation=False,
        sauvegarde_avant=True,
        handler=_handle_stop_run,
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
        Tous les mots après la commande sont joints (espace simple) et
        traités comme le premier paramètre requis de l'outil, s'il y en a
        un (ex. "/run mon super truc" -> {"feature_name": "mon super truc"}
        — un nom de feature peut contenir des espaces, à la différence d'un
        run_id). Mapping minimal à un seul paramètre requis pour toutes les
        commandes actuelles (S1/S2), à étoffer si un futur outil en attend
        plusieurs.
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
        args[required[0]] = " ".join(rest)
    return tool_name, args


async def execute_tool(
    name: str,
    args: dict[str, Any],
    *,
    config: StudioConfig,
    confirmed: bool = False,
    bot: Optional[Any] = None,
    chat_id: Optional[int] = None,
    message_thread_id: Optional[int] = None,
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
        message_thread_id: Identifiant du topic Telegram d'origine, transmis
            comme bot/chat_id — nécessaire aux outils qui doivent savoir dans
            quel topic agir au-delà de la config déjà résolue (ex.
            new_feature, ADR 0015, qui démarre un dialogue dans ce topic).
            None pour les outils qui n'en ont pas besoin.

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
        data = await spec.handler(
            config, bot=bot, chat_id=chat_id, message_thread_id=message_thread_id, **args,
        )
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
