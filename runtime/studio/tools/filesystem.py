"""
Opérations filesystem pour devaimazing.

Lecture et écriture des fiches .md, project-map, architect-map.
Injection des skills dans les prompts.
"""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from studio.routing import AGENT_TO_NODE
from studio.tools.tracer import AgentTracer, RAW_OUTPUT_HEAD_CHARS

FEEDBACK_HEADING = "## Feedback"
EMPTY_FEEDBACK_MARKER = "_Aucun feedback pour l'instant._"

# Contrat de sortie des agents producteurs de code (Back, Front, Test — voir
# prompts/backend.md, prompts/frontend.md, prompts/test.md, section "Format
# de sortie"). Délimiteurs distinctifs (pas des ``` markdown standards) pour
# éviter toute ambiguïté avec des blocs de code que l'agent citerait dans son
# raisonnement en dehors d'un vrai bloc fichier.
#
# Le contenu s'arrête au premier de : "<<<DEVAIMAZING_END>>>", le prochain
# "<<<DEVAIMAZING_FILE path=...>>>", ou la fin du texte — jamais seulement au
# premier (exiger explicitement <<<DEVAIMAZING_END>>>) : un agent qui oublie
# ce délimiteur de fermeture avant d'enchaîner sur le fichier suivant (gap
# constaté en run réel, todolist3, 2026-08-06 : qwen2.5:7b-instruct, dernier
# palier de la cascade) faisait sinon absorber tout le fichier suivant —
# délimiteur d'ouverture compris — comme contenu littéral du premier.
#
# ">{2,}" (pas juste ">>>" strict) : gap constaté le 2026-08-07 (todolist3,
# agent front, plusieurs modèles de la cascade) — le marqueur de fermeture
# du tag d'ouverture écrit avec 2 ">" au lieu de 3
# (`<<<DEVAIMAZING_FILE path="...">>`), jamais reconnu par un motif strict
# => tout le texte retombait en blocked_reason alors que le contenu du
# fichier était là, juste mal balisé.
_FILE_BLOCK_PATTERN = re.compile(
    r'<<<DEVAIMAZING_FILE path="([^"]+)">{2,}\n(.*?)'
    r'(?=\n<<<DEVAIMAZING_END>{2,}|\n<<<DEVAIMAZING_FILE path="|\Z)',
    re.DOTALL,
)

# Échappatoire de parse_agent_file_output (Back/Front/Test uniquement) pour
# le cas où l'agent détecte une impossibilité — remplace le champ
# blocked_reason du défunt contrat JSON (voir tools.ollama.run_ollama).
_BLOCKED_BLOCK_PATTERN = re.compile(
    r'<<<DEVAIMAZING_BLOCKED>{2,}\n(.*?)(?=\n<<<DEVAIMAZING_END>{2,}|\Z)',
    re.DOTALL,
)

# Repli de parse_agent_file_blocks quand l'agent produit un unique bloc de
# code balisé ``` (markdown standard) au lieu du contrat <<<DEVAIMAZING_FILE.
_FENCED_CODE_PATTERN = re.compile(r'```(?:\w+)?\n(.*?)\n```', re.DOTALL)

# Repli de parse_agent_file_output (voir _parse_markdown_fenced_files) : titre
# Markdown ATX (1 à 6 '#') dont le texte ressemble à un chemin de fichier
# relatif (segments séparés par '/', extension), optionnellement entouré de
# backticks — ex. "### backend/config.py" ou "#### `backend/tests/__init__.py`".
_MARKDOWN_FILE_HEADING_PATTERN = re.compile(
    r'(?m)^#{1,6}\s*`?([\w][\w./\-]*\.\w+)`?\s*$'
)

# Plafond du texte brut utilisé comme blocked_reason quand
# parse_agent_file_output ne trouve aucun bloc reconnu — voir sa docstring
# (repli "no_blocks"). Ce texte est écrit tel quel dans la fiche
# (append_feedback) et relu intégralement à chaque activation suivante ;
# sans plafond, une réponse de plusieurs milliers de tokens (agent qui
# répond en Markdown libre au lieu du contrat délimiteurs) grossit la fiche
# définitivement à chaque occurrence.
_NO_BLOCKS_FEEDBACK_MAX_CHARS = 1500

# Agents pouvant apparaître dans state.agent_sequence / structured_output du PM
# (tous les agents de AGENT_TO_NODE sauf pm/architect, qui n'y figurent jamais —
# voir studio.routing).
_PM_SEQUENCE_AGENTS = sorted(set(AGENT_TO_NODE) - {"pm", "architect"})

# Champs machine-only requis par fiche, remplis par le PM en phase 3 via
# structured_output (--json-schema) — voir PM_FICHES_SCHEMA,
# parse_pm_structured_output.
_CARD_METADATA_FIELDS = (
    "files_to_create", "files_to_modify", "files_forbidden",
    "existing_files_to_read", "dependencies",
)

# Schéma JSON transmis à tools.claude_code.run_claude_code(response_schema=...)
# pour l'appel du PM en phase 3 (voir docs/roadmap.md, chantier "Fiches PM en
# sortie structurée", 2026-07-14). Canal structuré parallèle au contrat prose
# existant (blocs <<<DEVAIMAZING_FILE>>>) : ne décrit que la séquence d'agents
# et, par agent, les listes de fichiers — jamais le contenu de la fiche
# elle-même (resté en Markdown libre côté prose).
PM_FICHES_SCHEMA = {
    "type": "object",
    "properties": {
        "sequence": {
            "type": "array",
            "items": {"type": "string", "enum": _PM_SEQUENCE_AGENTS},
        },
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "enum": _PM_SEQUENCE_AGENTS},
                    **{field: {"type": "array", "items": {"type": "string"}}
                       for field in _CARD_METADATA_FIELDS},
                },
                "required": ["agent", *_CARD_METADATA_FIELDS],
            },
        },
    },
    "required": ["sequence", "cards"],
}


def _validate_relative_path(path: str) -> None:
    """
    Garde-fou appliqué à tout chemin de fichier produit par un agent
    (parse_agent_file_output, parse_agent_file_blocks) avant qu'il ne
    serve à construire un chemin d'écriture (config.repo_path / path).

    pathlib ignore silencieusement le premier opérande d'un `/` dès que le
    second est absolu (`Path("/repo") / "/etc/passwd" == Path("/etc/passwd")`)
    — un chemin absolu produit par l'agent contournerait donc repo_path
    entièrement. Trouvé en pratique (2026-07-14, voir docs/roadmap.md) :
    qwen2.5:1.5b-instruct a produit `"path": "/backend/main.py"` (slash de
    tête, imitation littérale de la formulation "/backend/" dans
    prompts/backend.md) — écriture tentée hors du repo cible, bloquée par un
    PermissionError du système d'exploitation, pas par devaimazing.

    Args:
        path: Chemin tel que renvoyé par l'agent (censé être relatif au repo
            cible).

    Raises:
        ValueError: Si `path` est vide, absolu, ou contient un composant
            ".." (traversée de répertoire) — dans les deux cas, le chemin
            résultant pourrait s'écrire hors du repo cible.
    """
    if not path or not path.strip():
        raise ValueError("Chemin de fichier vide produit par l'agent")
    candidate = Path(path)
    if candidate.is_absolute():
        raise ValueError(
            f"Chemin de fichier absolu rejeté : {path!r} — attendu un chemin relatif "
            "au repo cible (ex. 'backend/main.py'), pas un chemin commençant par '/'"
        )
    if ".." in candidate.parts:
        raise ValueError(
            f"Chemin de fichier avec traversée de répertoire ('..') rejeté : {path!r}"
        )


async def read_card(card_path: Path, tracer: Optional[AgentTracer] = None) -> str:
    """
    Lit une fiche .md.

    Args:
        card_path: Chemin absolu vers la fiche.
        tracer: AgentTracer optionnel (voir tools.tracer) — émet
            "card_read" en cas de succès, "error" si le fichier est
            introuvable. `None` (défaut) : aucune trace émise.

    Returns:
        Contenu de la fiche en texte.

    Raises:
        FileNotFoundError: Si la fiche n'existe pas.
    """
    if not card_path.is_file():
        if tracer is not None:
            tracer.emit("error", event_source="read_card", path=str(card_path))
        raise FileNotFoundError(f"Fiche introuvable : {card_path}")
    content = card_path.read_text(encoding="utf-8")
    if tracer is not None:
        tracer.emit("card_read", path=str(card_path), chars=len(content))
    return content


async def write_card(card_path: Path, content: str, tracer: Optional[AgentTracer] = None) -> None:
    """
    Écrit ou écrase une fiche .md.

    Args:
        card_path: Chemin absolu vers la fiche.
        content: Contenu Markdown à écrire.
        tracer: AgentTracer optionnel (voir tools.tracer) — émet
            "card_written" une fois l'écriture faite. `None` (défaut) :
            aucune trace émise.

    Side effects:
        Crée ou écrase le fichier. Crée les répertoires parents si nécessaire.
    """
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(content, encoding="utf-8")
    if tracer is not None:
        tracer.emit("card_written", path=str(card_path), chars=len(content))


def strip_feedback_section(card_content: str) -> str:
    """
    Retire la section Feedback (et tout ce qui suit) du contenu d'une
    fiche — garde Contexte/Tâche/Critères de livraison, retire l'historique
    de feedback cumulé.

    Utilisé pour le mode correction ciblée (voir studio.state.StudioState.
    retry_scope) : après un échec identifié avec certitude (tools.pyenv.
    verify_python_files), le tour suivant n'a besoin que du fichier fautif
    + son message d'erreur précis, pas de tout l'historique de feedback
    (qui grossit sans borne — gap trouvé en run le 2026-07-20, un feedback
    à lui seul avait déjà atteint 2792 caractères).

    Example:
        >>> strip_feedback_section("## Tâche\\nFais X.\\n\\n## Feedback\\n[...] : Y")
        '## Tâche\\nFais X.\\n\\n'
    """
    heading_index = card_content.find(FEEDBACK_HEADING)
    if heading_index == -1:
        return card_content
    return card_content[:heading_index]


async def append_feedback(card_path: Path, agent_source: str, feedback: str) -> None:
    """
    Ajoute une entrée dans la section Feedback d'une fiche.

    Args:
        card_path: Chemin absolu vers la fiche.
        agent_source: Nom de l'agent qui donne le feedback.
        feedback: Texte du feedback.

    Side effects:
        Modifie la fiche en place.

    Raises:
        FileNotFoundError: Si la fiche n'existe pas.
        ValueError: Si la fiche ne contient pas de section Feedback.
    """
    if not card_path.is_file():
        raise FileNotFoundError(f"Fiche introuvable : {card_path}")

    content = card_path.read_text(encoding="utf-8")

    heading_index = content.find(FEEDBACK_HEADING)
    if heading_index == -1:
        raise ValueError(
            f"La fiche {card_path} ne contient pas de section '{FEEDBACK_HEADING}'"
        )

    section_start = heading_index + len(FEEDBACK_HEADING)
    next_heading_index = content.find("\n## ", section_start)
    section_end = next_heading_index if next_heading_index != -1 else len(content)

    section = content[section_start:section_end]
    section = section.replace(EMPTY_FEEDBACK_MARKER, "").rstrip("\n")

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry = f"[{date_str}] [{agent_source}] : {feedback}"
    new_section = f"{section}\n{entry}\n" if section.strip() else f"\n{entry}\n"

    new_content = content[:section_start] + new_section + content[section_end:]
    card_path.write_text(new_content, encoding="utf-8")


async def read_files(
    repo_path: Path, paths: list[str], tracer: Optional[AgentTracer] = None
) -> str:
    """
    Lit le contenu de fichiers existants du repo cible, chemins donnés
    explicitement (typiquement state.agent_card_metadata[role]
    ["existing_files_to_read"], voir parse_pm_structured_output).

    Args:
        repo_path: Racine du repo projet cible.
        paths: Chemins relatifs à lire, dans l'ordre où ils doivent apparaître
            dans le contexte retourné.
        tracer: AgentTracer optionnel (voir tools.tracer) — émet
            "referenced_files_resolved" (requested/found) si `paths` est non
            vide et que tous les chemins sont lus avec succès, "error"
            (requested/found/missing) si un chemin est introuvable. `None`
            (défaut) : aucune trace émise.

    Returns:
        Contenu concaténé des fichiers, chacun précédé d'un titre indiquant
        son chemin exact. Chaîne vide si `paths` est vide (cas normal : run
        qui ne fait que créer des fichiers, rien à lire au préalable).

    Raises:
        FileNotFoundError: Si un chemin de `paths` n'existe pas sur disque —
            remplace l'ancien comportement de read_referenced_files (skip
            silencieux). Un chemin listé par le PM en phase 3 est garanti
            exister au moment de l'écriture de la fiche (validation dans
            nodes.pm._run_fiches), donc une absence ici signale une
            incohérence réelle plutôt qu'un cas normal à ignorer.

    Notes:
        Remplace read_referenced_files (scan regex du texte prose de la
        fiche, supprimé) — voir docs/roadmap.md, chantier "Fiches PM en
        sortie structurée" (2026-07-14). Corrige le bug qui motivait ce
        chantier : un chemin référencé inexistant n'est plus ignoré
        silencieusement (l'agent producteur, Qwen, contexte limité,
        hallucinait alors le contenu du fichier) — il est désormais
        impossible d'atteindre ce cas, la validation ayant eu lieu à
        l'écriture de la fiche par le PM, pas ici à la lecture.

    Example:
        >>> content = await read_files(
        ...     Path("/home/user/code/demo"), ["backend/main.py"]
        ... )
    """
    parts = []
    found: list[str] = []
    for relative_path in paths:
        try:
            content = await read_card(repo_path / relative_path)
        except FileNotFoundError:
            if tracer is not None:
                tracer.emit(
                    "error", event_source="read_files",
                    requested=paths, found=found, missing=relative_path,
                )
            raise
        found.append(relative_path)
        parts.append(f"### Contenu actuel de `{relative_path}`\n\n```\n{content}\n```")
    if tracer is not None and paths:
        tracer.emit("referenced_files_resolved", requested=paths, found=found)
    return "\n\n".join(parts)


def parse_pm_structured_output(
    structured_output: Optional[dict],
) -> tuple[list[str], dict[str, dict[str, list[str]]]]:
    """
    Valide et extrait le structured_output du PM en phase 3 (voir
    PM_FICHES_SCHEMA, tools.claude_code.run_claude_code(response_schema=...)).

    Args:
        structured_output: Champ "structured_output" du retour de
            run_claude_code — None si l'appel n'a pas fourni response_schema,
            ou si le CLI n'a pas produit ce champ malgré le schéma demandé
            (pas garanti à 100%, voir docs/roadmap.md).

    Returns:
        (sequence, cards_metadata). `sequence` : liste ordonnée des agents,
        telle que produite par le PM. `cards_metadata` : mapping agent ->
        {"files_to_create", "files_to_modify", "files_forbidden",
        "existing_files_to_read", "dependencies"} (chacune une list[str]),
        une entrée par agent de `sequence`.

    Raises:
        ValueError: Si structured_output est None, n'est pas un dict, si
            "sequence"/"cards" sont absents ou mal typés, si un agent de
            `sequence` n'a pas d'entrée correspondante dans `cards`, ou si un
            des 5 champs machine-only d'une entrée cards n'est pas une
            list[str]. Message actionnable référençant prompts/pm.md — cette
            fonction est appelée avant toute écriture de fiche sur disque
            (nodes.pm._run_fiches), conformément à l'objectif de valider à
            l'écriture plutôt qu'à la lecture (voir docs/roadmap.md).

    Example:
        >>> sequence, cards = parse_pm_structured_output({
        ...     "sequence": ["back"],
        ...     "cards": [{
        ...         "agent": "back", "files_to_create": ["backend/main.py"],
        ...         "files_to_modify": [], "files_forbidden": [],
        ...         "existing_files_to_read": [], "dependencies": [],
        ...     }],
        ... })
        >>> sequence
        ['back']
    """
    if not isinstance(structured_output, dict):
        raise ValueError(
            "structured_output absent ou invalide dans la réponse du PM (phase 3) — "
            "attendu un objet JSON conforme à PM_FICHES_SCHEMA (voir prompts/pm.md, "
            "section Format de sortie — phase 3)"
        )

    sequence = structured_output.get("sequence")
    cards = structured_output.get("cards")
    if not isinstance(sequence, list) or not sequence or not all(
        isinstance(agent, str) for agent in sequence
    ):
        raise ValueError(
            "structured_output.sequence absent, vide ou mal typé dans la réponse du PM "
            "(phase 3) — attendu une liste non vide de noms d'agent (voir prompts/pm.md, "
            "section Format de sortie — phase 3)"
        )
    if not isinstance(cards, list):
        raise ValueError(
            "structured_output.cards absent ou mal typé dans la réponse du PM (phase 3) — "
            "attendu une liste d'objets {agent, files_to_create, ...} (voir prompts/pm.md, "
            "section Format de sortie — phase 3)"
        )

    cards_by_agent: dict[str, dict[str, list[str]]] = {}
    for card in cards:
        if not isinstance(card, dict) or "agent" not in card:
            raise ValueError(
                f"Entrée structured_output.cards incomplète (champ 'agent' attendu), "
                f"reçu : {card!r} (voir prompts/pm.md, section Format de sortie — phase 3)"
            )
        agent = card["agent"]
        metadata: dict[str, list[str]] = {}
        for field_name in _CARD_METADATA_FIELDS:
            values = card.get(field_name)
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise ValueError(
                    f"structured_output.cards[{agent!r}].{field_name} mal typé (liste de "
                    f"chaînes attendue), reçu : {values!r} (voir prompts/pm.md, section "
                    "Format de sortie — phase 3)"
                )
            metadata[field_name] = values
        cards_by_agent[agent] = metadata

    for agent in sequence:
        if agent not in cards_by_agent:
            raise ValueError(
                f"Agent {agent!r} présent dans structured_output.sequence mais sans entrée "
                "correspondante dans structured_output.cards (voir prompts/pm.md, section "
                "Format de sortie — phase 3)"
            )

    return sequence, cards_by_agent


async def inject_skills(base_prompt: str, skill_names: list[str], skills_dir: Path) -> str:
    """
    Injecte les skills dans un prompt système.

    Args:
        base_prompt: Contenu de prompts/<agent>.md.
        skill_names: Liste des noms de skills à injecter (sans extension).
        skills_dir: Répertoire contenant les fichiers skill .md.

    Returns:
        Prompt enrichi avec les skills en appendice.

    Raises:
        FileNotFoundError: Si un skill n'existe pas.

    Example:
        >>> prompt = await inject_skills(
        ...     base_prompt="Tu es l'agent Backend...",
        ...     skill_names=["stub-first", "error-handling"],
        ...     skills_dir=Path("/home/user/devaimazing/skills"),
        ... )
    """
    parts = [base_prompt]
    for skill_name in skill_names:
        skill_path = skills_dir / f"{skill_name}.md"
        if not skill_path.is_file():
            raise FileNotFoundError(f"Skill introuvable : {skill_path}")
        parts.append(f"\n\n---\n\n{skill_path.read_text(encoding='utf-8')}")
    return "".join(parts)


def parse_agent_file_blocks(
    text: str, fallback_path: Optional[str] = None, tracer: Optional[AgentTracer] = None
) -> dict[str, str]:
    """
    Extrait les blocs de fichiers du contrat de sortie des agents
    producteurs de code (voir prompts/backend.md, prompts/frontend.md,
    prompts/test.md, section "Format de sortie").

    Format attendu par bloc :
        <<<DEVAIMAZING_FILE path="chemin/relatif/fichier.py">>>
        <contenu du fichier>
        <<<DEVAIMAZING_END>>>

    Args:
        text: Sortie brute générée par l'agent (champ "content" du retour
            de tools.ollama.run_ollama).
        fallback_path: Si fourni et qu'aucun bloc <<<DEVAIMAZING_FILE>>>
            n'est trouvé, mais que `text` contient un unique bloc de code
            balisé ``` (markdown standard), ce bloc est associé à
            fallback_path plutôt que de lever ValueError (voir Notes).
        tracer: AgentTracer optionnel (voir tools.tracer) — émet
            "parse_output" (outcome success/error), avec raw_output_head en
            cas d'erreur. `None` (défaut) : aucune trace émise.

    Returns:
        Mapping chemin relatif -> contenu du fichier, dans l'ordre
        d'apparition dans `text`. Si plusieurs blocs déclarent le même
        chemin, le dernier l'emporte.

    Raises:
        ValueError: Si `text` ne contient aucun bloc de fichier reconnu, et
            que le repli (fallback_path) ne s'applique pas non plus (absent,
            ou `text` contient zéro ou plusieurs blocs ``` — ambigu, pas de
            devinette dans ce cas). Également si un chemin de fichier
            (déclaré dans un bloc, ou fallback_path) est absolu ou contient
            une traversée de répertoire ('..') — voir _validate_relative_path.

    Notes:
        Repli ajouté suite à un run réel (2026-07-11, voir docs/roadmap.md) :
        un modèle producteur local (Qwen, contexte limité, imitatif) peut
        produire un contenu de fichier correct mais balisé en ``` markdown
        standard au lieu du contrat <<<DEVAIMAZING_FILE>>>, de façon
        répétée malgré un prompt explicite — observé sur 3/3 tentatives d'un
        run réel. Le repli ne s'applique que si l'appelant sait déjà, par un
        autre moyen (ex : un seul chemin mentionné dans la fiche source),
        quel fichier unique est attendu — jamais de devinette si plusieurs
        blocs ``` ou plusieurs chemins candidats.

    Example:
        >>> parse_agent_file_blocks(
        ...     '<<<DEVAIMAZING_FILE path="backend/a.py">>>\\n'
        ...     'print(1)\\n'
        ...     '<<<DEVAIMAZING_END>>>'
        ... )
        {'backend/a.py': 'print(1)'}
    """
    try:
        files = _parse_agent_file_blocks(text, fallback_path)
    except ValueError:
        if tracer is not None:
            tracer.emit(
                "parse_output", parser="parse_agent_file_blocks", outcome="error",
                raw_output_head=text[:RAW_OUTPUT_HEAD_CHARS],
            )
        raise
    if tracer is not None:
        tracer.emit(
            "parse_output", parser="parse_agent_file_blocks", outcome="success",
            files=sorted(files),
        )
    return files


def _parse_agent_file_blocks(text: str, fallback_path: Optional[str]) -> dict[str, str]:
    matches = _FILE_BLOCK_PATTERN.findall(text)
    if matches:
        for path, _content in matches:
            _validate_relative_path(path)
        return {path: content for path, content in matches}

    if fallback_path is not None:
        _validate_relative_path(fallback_path)
        fenced_blocks = _FENCED_CODE_PATTERN.findall(text)
        if len(fenced_blocks) == 1:
            return {fallback_path: fenced_blocks[0].strip()}

    raise ValueError(
        "Aucun bloc de fichier reconnu dans la sortie de l'agent "
        '(format attendu : <<<DEVAIMAZING_FILE path="...">>> ... <<<DEVAIMAZING_END>>>)'
    )


def _parse_markdown_fenced_files(text: str) -> dict[str, str]:
    """
    Repli de dernier recours de parse_agent_file_output, essayé quand aucun
    bloc <<<DEVAIMAZING_FILE>>> n'est trouvé : reconnaît le motif Markdown
    "titre = chemin de fichier" suivi d'un bloc de code balisé ``` (ex.
    "### backend/config.py" puis ```python\\n...\\n```), utilisé par des
    modèles locaux qui ignorent le contrat de délimiteurs mais restent
    structurés en Markdown standard — motif dominant parmi les échecs
    "no_blocks" observés en run réel (todolist3, 2026-08-06/07 : 9 des 14
    occurrences suivaient ce format).

    Un titre non suivi d'un bloc balisé avant le titre suivant (ou la fin du
    texte) est ignoré — rien à récupérer. Si un même chemin apparaît
    plusieurs fois, la dernière occurrence l'emporte (cohérent avec le
    contrat <<<DEVAIMAZING_FILE>>>). Un chemin invalide (absolu, traversée de
    répertoire — voir _validate_relative_path) est ignoré plutôt que de faire
    échouer tout le repli : c'est déjà un mode dégradé, mieux vaut récupérer
    ce qui est sûr que de tout perdre pour une seule entrée suspecte.

    Args:
        text: Sortie brute de l'agent, déjà écartée du contrat
            <<<DEVAIMAZING_FILE>>> par l'appelant.

    Returns:
        Mapping chemin relatif -> contenu de fichier. Vide si aucun titre
        n'est suivi d'un bloc balisé.
    """
    headings = list(_MARKDOWN_FILE_HEADING_PATTERN.finditer(text))
    files: dict[str, str] = {}
    for index, match in enumerate(headings):
        path = match.group(1)
        chunk_start = match.end()
        chunk_end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        fence_match = _FENCED_CODE_PATTERN.search(text[chunk_start:chunk_end])
        if fence_match is None:
            continue
        try:
            _validate_relative_path(path)
        except ValueError:
            continue
        files[path] = fence_match.group(1).strip()
    return files


def parse_agent_file_output(
    text: str, tracer: Optional[AgentTracer] = None
) -> tuple[dict[str, str], str]:
    """
    Parse la sortie d'un agent producteur (Back/Front/Test) appelé sans
    contrainte de schéma JSON — contrat texte par délimiteurs
    <<<DEVAIMAZING_FILE>>> (voir prompts/backend.md, frontend.md, test.md,
    section "Format de sortie"). Remplace parse_structured_file_output
    (JSON contraint par grammaire, introduit le 2026-07-11) depuis le
    2026-08-06 : contraindre le contenu d'un fichier à être une valeur de
    chaîne JSON dégradait mesurablement la fidélité de génération (perte de
    syntaxe des commentaires — `//` au lieu de `#`, perte d'indentation
    après les deux-points) — voir docs/roadmap.md, expérience A/B du
    2026-08-06 (0/4 générations propres en JSON contraint contre 1/1 en
    texte délimité, même modèle même prompt, même fiche réelle todolist3).

    Args:
        text: Sortie brute générée par l'agent (champ "content" du retour
            de tools.ollama.run_ollama, appelé sans response_format).
        tracer: AgentTracer optionnel (voir tools.tracer) — émet
            "parse_output" (outcome success/blocked/no_blocks), avec
            raw_output_head si no_blocks. `None` (défaut) : aucune trace
            émise.

    Returns:
        (files, blocked_reason). Un bloc <<<DEVAIMAZING_BLOCKED>>> présent
        prime sur tout bloc fichier (l'agent a explicitement signalé un
        blocage) : files vide, blocked_reason = contenu du bloc. Sinon, les
        blocs <<<DEVAIMAZING_FILE>>> sont extraits normalement (files non
        vide, blocked_reason=""). Si aucun bloc <<<DEVAIMAZING_FILE>>> n'est
        trouvé, un repli Markdown est tenté (voir
        _parse_markdown_fenced_files) : titres "### chemin/fichier.py"
        suivis d'un bloc balisé ``` — si au moins un titre a un bloc balisé
        associé, ces fichiers sont retournés normalement (blocked_reason="").
        En tout dernier recours, si rien de tout cela n'est trouvé, le texte
        brut (tronqué à _NO_BLOCKS_FEEDBACK_MAX_CHARS) devient blocked_reason
        — repli mieux qu'un crash sur une sortie qui n'a suivi aucun format,
        mais borné : ce texte est écrit tel quel dans la section Feedback de
        la fiche (tools.filesystem.append_feedback) et relu intégralement à
        chaque activation suivante — sans plafond, un agent qui répond
        parfois en Markdown libre au lieu du contrat délimiteurs (gap
        constaté en run réel, todolist3, 2026-08-07) fait grossir la fiche
        indéfiniment à chaque occurrence, jusqu'à des prompts de 20k+
        tokens et des timeouts Ollama en cascade.

    Raises:
        ValueError: Si un "path" de bloc <<<DEVAIMAZING_FILE>>> est absolu
            ou contient une traversée de répertoire ('..') — voir
            _validate_relative_path ; trouvé en pratique (2026-07-14,
            qwen2.5:1.5b-instruct produisant "/backend/main.py").

    Example:
        >>> parse_agent_file_output(
        ...     '<<<DEVAIMAZING_FILE path="backend/a.py">>>\\n'
        ...     'x = 1\\n<<<DEVAIMAZING_END>>>'
        ... )
        ({'backend/a.py': 'x = 1'}, '')
    """
    blocked_match = _BLOCKED_BLOCK_PATTERN.search(text)
    if blocked_match:
        if tracer is not None:
            tracer.emit("parse_output", parser="parse_agent_file_output", outcome="blocked")
        return {}, blocked_match.group(1).strip()

    matches = _FILE_BLOCK_PATTERN.findall(text)
    if matches:
        for path, _content in matches:
            _validate_relative_path(path)
        files = {path: content for path, content in matches}
        if tracer is not None:
            tracer.emit(
                "parse_output", parser="parse_agent_file_output", outcome="success",
                files=sorted(files), blocked=False,
            )
        return files, ""

    markdown_files = _parse_markdown_fenced_files(text)
    if markdown_files:
        if tracer is not None:
            tracer.emit(
                "parse_output", parser="parse_agent_file_output", outcome="markdown_fallback",
                files=sorted(markdown_files),
            )
        return markdown_files, ""

    if tracer is not None:
        tracer.emit(
            "parse_output", parser="parse_agent_file_output", outcome="no_blocks",
            raw_output_head=text[:RAW_OUTPUT_HEAD_CHARS],
        )
    stripped = text.strip()
    if len(stripped) > _NO_BLOCKS_FEEDBACK_MAX_CHARS:
        stripped = (
            stripped[:_NO_BLOCKS_FEEDBACK_MAX_CHARS]
            + f"\n[...tronqué, {len(stripped)} caractères au total — "
            "aucun bloc <<<DEVAIMAZING_FILE>>> reconnu dans la réponse]"
        )
    return {}, stripped
