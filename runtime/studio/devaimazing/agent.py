"""
Agent Devaimazing — compréhension du langage naturel (ADR 0013, tranche S4).

Function-calling natif Ollama (`tools=...`) n'est supporté par aucune variante
de Gemma 3 (confirmé empiriquement et via la doc officielle, voir
docs/roadmap.md, gate du 2026-07-24). `run_devaimazing_turn` utilise donc le
fallback structured-output (`tools.ollama.DEVAIMAZING_TURN_SCHEMA`) déjà
employé par Back/Front/Test pour leur sortie fichier — même mécanisme, schéma
différent.

Le prompt système est composé de deux parties concaténées :
- `prompts/devaimazing.md` (identité, rôle, ce que l'agent fait/ne fait pas —
  contenu stable, indépendant du registre d'outils) ;
- une énumération stricte des outils générée depuis `TOOL_REGISTRY`
  (`_build_tool_directive`) — jamais recopiée à la main dans le fichier
  markdown, pour que le prompt ne puisse pas dériver du registre réel.

La formulation de `_build_tool_directive` a été affinée par plusieurs cycles
de validation empirique le 2026-07-29 (voir docs/roadmap.md pour le détail et
la méthode) — ne pas la reformuler sans revalider :
- une description en prose des outils laisse gemma3:4b halluciner une réponse
  directe au lieu d'appeler un outil sans paramètre (`lister_projets`, 0/4) ;
  une énumération stricte avec noms exacts entre guillemets et une règle de
  décision en 2 étapes corrige ce cas (11/11 sur un registre-jouet à 2 outils).
- sur le registre réel à 7 outils, un cas reproductible (2/2) a fait dériver
  la génération de `reply` en boucle de répétition jusqu'à corrompre tout le
  JSON — y compris un `tool_call` par ailleurs correct. Réordonner le schéma
  (`tool_call` avant `reply`) corrige la dérive mais casse la discrimination
  outil/pas-outil (le modèle se met à remplir tool_call sur du pur bavardage,
  y compris un outil destructif sur "Merci pour ton aide !"). La consigne
  retenue — reply="" obligatoire dès que tool_call est rempli, ordre du
  schéma inchangé — corrige la dérive (5/5 sur le cas reproductible) sans
  casser la discrimination.
"""

import json
from pathlib import Path
from typing import Any, Optional

from studio.config import StudioConfig
from studio.tools.ollama import DEVAIMAZING_TURN_SCHEMA, run_ollama
from studio.tools.registry import TOOL_REGISTRY, ToolSpec, execute_tool, format_tool_result

_DEVAIMAZING_ROOT = Path(__file__).resolve().parents[3]
_PROMPT_PATH = _DEVAIMAZING_ROOT / "prompts" / "devaimazing.md"


def _build_tool_directive(tools: dict[str, ToolSpec]) -> str:
    """
    Génère l'énumération stricte des outils consommée par le prompt système,
    dans le style validé empiriquement le 2026-07-29 (voir module docstring).
    """
    lines = [
        'Liste EXACTE des outils disponibles (utilise le nom exact, caractere pour '
        "caractere) :",
    ]
    for spec in tools.values():
        required = spec.parameters.get("required", [])
        properties = spec.parameters.get("properties", {})
        if not properties:
            params_desc = "aucun parametre, mais l'outil DOIT quand meme etre appele"
        else:
            params_desc = ", ".join(
                f"{name}: {properties.get(name, {}).get('type', 'string')}"
                f"{' (obligatoire)' if name in required else ''}"
                for name in properties
            )
        lines.append(f'- "{spec.name}" — parametres: {{{params_desc}}} — {spec.description}')

    return (
        "Tu n'as AUCUNE connaissance directe des projets ou des runs en cours. Tu ne "
        "connais JAMAIS de nom de projet ou de statut par toi-meme : la seule facon "
        "d'obtenir cette information est d'appeler un outil. Si tu reponds avec des "
        "noms de projets ou un statut sans avoir appele l'outil correspondant, ta "
        "reponse est une hallucination et donc fausse.\n\n"
        + "\n".join(lines)
        + "\n\n"
        'Format de reponse OBLIGATOIRE, JSON valide conforme au schema :\n'
        '{"reply": "<ton message texte>", "tool_call": {"name": "<nom_outil>", '
        '"arguments": {...}} ou null}\n\n'
        "Si tool_call est rempli, reply DOIT etre une chaine vide (\"\") — ne "
        "genere aucun texte dans reply dans ce cas, il ne sera pas utilise et "
        "generer du texte inutile augmente le risque d'erreur.\n\n"
        "Regle de decision stricte :\n"
        "1. La question porte-t-elle sur un run ou sur les projets ? Si oui -> "
        "tool_call rempli avec le nom EXACT de l'outil ci-dessus, jamais de reponse "
        "directe.\n"
        "2. Sinon (question generale, salutation, remerciement) -> tool_call est "
        "null et reply contient ta reponse en texte libre."
    )


def build_system_prompt(tools: dict[str, ToolSpec] = TOOL_REGISTRY) -> str:
    """Prompt système complet : identité (prompts/devaimazing.md) + outils (registre)."""
    persona = _PROMPT_PATH.read_text(encoding="utf-8")
    return f"{persona}\n\n---\n\n{_build_tool_directive(tools)}"


def parse_devaimazing_turn(content: str) -> tuple[str, Optional[dict[str, Any]]]:
    """
    Parse la sortie structurée d'un tour Devaimazing (voir DEVAIMAZING_TURN_SCHEMA).

    Args:
        content: Sortie JSON brute (champ "content" du retour de run_ollama,
            appelé avec response_format=DEVAIMAZING_TURN_SCHEMA).

    Returns:
        (reply, tool_call). `tool_call` est None si l'agent n'a pas jugé
        nécessaire d'appeler un outil, sinon {"name": str, "arguments": dict}
        — le nom n'est PAS encore validé contre TOOL_REGISTRY à ce stade
        (voir run_devaimazing_turn, qui délègue cette validation à
        execute_tool).

    Raises:
        ValueError: Si `content` n'est pas un JSON valide, ou si sa
            structure ne correspond pas au schéma attendu.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Sortie Devaimazing invalide (JSON attendu) : {exc}") from exc

    if not isinstance(data, dict) or "reply" not in data or "tool_call" not in data:
        raise ValueError(
            "Sortie Devaimazing incomplète : champs 'reply' et 'tool_call' attendus "
            f"(voir tools.ollama.DEVAIMAZING_TURN_SCHEMA), reçu : {content!r}"
        )

    tool_call = data["tool_call"]
    if tool_call is not None:
        if (
            not isinstance(tool_call, dict)
            or "name" not in tool_call
            or "arguments" not in tool_call
            or not isinstance(tool_call["arguments"], dict)
        ):
            raise ValueError(f"tool_call mal formé : {tool_call!r}")

    return data["reply"], tool_call


async def interpret_message(
    text: str,
    *,
    model: str,
    base_url: str = "http://localhost:11434",
    num_ctx: int = 4096,
) -> tuple[str, Optional[dict[str, Any]]]:
    """
    Appelle Gemma en sortie structurée pour interpréter un message utilisateur,
    sans dispatcher vers le registre d'outils (voir dispatch_tool_call) — ne
    prend donc pas de `config` en argument. Séparé de run_devaimazing_turn
    pour permettre à l'appelant de résoudre `config` une fois le `tool_call`
    (donc le projet concerné) connu, plutôt qu'en amont de l'appel LLM (voir
    telegram.handlers.handle_natural_language, qui ne connaît le topic/projet
    qu'une fois le nom d'outil déterminé).

    Args:
        text: Message utilisateur (déjà transcrit si vocal — voir ADR 0014,
            Devaimazing ne distingue jamais l'origine du texte).
        model: Identifiant du modèle Ollama (voir config/studio.yml,
            models.devaimazing).
        base_url: URL de l'API Ollama.
        num_ctx: Fenêtre de contexte — le prompt système généré (persona +
            7 outils) est nettement plus long qu'un prompt agent classique à
            2048 tokens (défaut Ollama, voir tools.ollama.run_ollama) ; 4096
            est la valeur testée empiriquement le 2026-07-29 (voir
            docs/roadmap.md et config/studio.yml, devaimazing.num_ctx), à
            ajuster si le registre grossit encore.

    Returns:
        (reply, tool_call). Si `tool_call` est None, `reply` est le message
        à renvoyer tel quel (texte libre du modèle, ou message de repli si
        la sortie n'a pas pu être parsée). Si `tool_call` est non None,
        `reply` ne doit PAS être utilisé (voir dispatch_tool_call) — le nom
        d'outil n'est pas encore validé contre TOOL_REGISTRY à ce stade.
    """
    system_prompt = build_system_prompt()
    result = await run_ollama(
        system_prompt=system_prompt,
        user_prompt=text,
        model=model,
        base_url=base_url,
        num_ctx=num_ctx,
        response_format=DEVAIMAZING_TURN_SCHEMA,
    )

    try:
        reply, tool_call = parse_devaimazing_turn(result["content"])
    except ValueError:
        return "Je n'ai pas réussi à traiter cette demande, peux-tu reformuler ?", None

    return reply, tool_call


async def dispatch_tool_call(
    tool_name: str,
    args: dict[str, Any],
    *,
    config: StudioConfig,
    confirmed: bool = False,
    bot: Optional[Any] = None,
    chat_id: Optional[int] = None,
    message_thread_id: Optional[int] = None,
) -> tuple[str, Optional[tuple[str, dict[str, Any]]]]:
    """
    Exécute un tool_call issu d'interpret_message via le registre d'outils
    partagé — même point d'appel (execute_tool) que
    telegram.handlers.handle_slash_command, pour ne pas dupliquer la logique
    de dispatch entre les deux voies d'entrée (voir ADR 0013, Décision 4).

    Args:
        tool_name: Nom d'outil renvoyé par le modèle — PAS encore validé
            contre TOOL_REGISTRY (delégué à execute_tool : un nom inconnu ou
            halluciné redescend en ToolResult(status="error"), jamais un
            appel de handler).
        args: Arguments de l'outil.
        config: Configuration du projet concerné (résolue par l'appelant une
            fois tool_name connu — voir interpret_message).
        confirmed: True si l'utilisateur vient de confirmer une action en
            attente (voir valeur de retour ci-dessous).
        bot: Contexte Telegram optionnel, transmis à execute_tool.
        chat_id: chat_id Telegram, transmis à execute_tool.

    Returns:
        (texte, en_attente). `texte` est le message à renvoyer tel quel.
        `en_attente` est None sauf si l'outil sélectionné requiert une
        confirmation non encore donnée : dans ce cas c'est (nom_outil, args),
        à conserver par l'appelant (même mécanisme que
        telegram.handlers._pending_confirmations) pour rappeler
        dispatch_tool_call avec confirmed=True après accord explicite.
    """
    tool_result = await execute_tool(
        tool_name, args, config=config, confirmed=confirmed, bot=bot, chat_id=chat_id,
        message_thread_id=message_thread_id,
    )

    if tool_result.status == "needs_confirmation":
        return tool_result.summary, (tool_name, args)

    return format_tool_result(tool_result), None


async def run_devaimazing_turn(
    text: str,
    *,
    config: StudioConfig,
    model: str,
    base_url: str = "http://localhost:11434",
    num_ctx: int = 4096,
    confirmed: bool = False,
    bot: Optional[Any] = None,
    chat_id: Optional[int] = None,
    message_thread_id: Optional[int] = None,
) -> tuple[str, Optional[tuple[str, dict[str, Any]]]]:
    """
    Exécute un tour de conversation Devaimazing complet : interpret_message
    puis dispatch_tool_call si nécessaire — composition des deux pour les
    appelants qui connaissent déjà `config` avant l'appel LLM (ex. un topic
    de projet déjà résolu). Voir handle_natural_language pour le cas General,
    où `config` n'est résolu qu'après avoir vu le tool_call.

    Notes:
        Le champ "reply" de la sortie du modèle n'est utilisé QUE si
        tool_call est null. Quand un outil est sélectionné, le texte renvoyé
        vient de ToolResult (via tools.registry.format_tool_result), jamais
        de "reply" — la validation du 2026-07-29 a montré "reply" halluciné
        (ex. noms de projets inventés) y compris quand tool_call.name était
        correct.
    """
    reply, tool_call = await interpret_message(
        text, model=model, base_url=base_url, num_ctx=num_ctx,
    )

    if tool_call is None:
        return reply, None

    return await dispatch_tool_call(
        tool_call["name"], tool_call["arguments"],
        config=config, confirmed=confirmed, bot=bot, chat_id=chat_id,
        message_thread_id=message_thread_id,
    )
