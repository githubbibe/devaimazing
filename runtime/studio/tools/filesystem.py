"""
Opérations filesystem pour devaimazing.

Lecture et écriture des fiches .md, project-map, architect-map.
Injection des skills dans les prompts.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

FEEDBACK_HEADING = "## Feedback"
EMPTY_FEEDBACK_MARKER = "_Aucun feedback pour l'instant._"

# Contrat de sortie des agents producteurs de code (Back, Front, Test — voir
# prompts/backend.md, prompts/frontend.md, prompts/test.md, section "Format
# de sortie"). Délimiteurs distinctifs (pas des ``` markdown standards) pour
# éviter toute ambiguïté avec des blocs de code que l'agent citerait dans son
# raisonnement en dehors d'un vrai bloc fichier.
_FILE_BLOCK_PATTERN = re.compile(
    r'<<<DEVAIMAZING_FILE path="([^"]+)">>>\n(.*?)\n<<<DEVAIMAZING_END>>>',
    re.DOTALL,
)

# Détection de chemins de fichiers référencés dans une fiche agent (ex :
# section "Fichiers à modifier"). Chemin entre backticks avec extension de
# fichier reconnue — voir read_referenced_files, extract_file_paths.
_REFERENCED_FILE_PATTERN = re.compile(
    r'`([\w./-]+\.(?:py|ts|tsx|js|jsx|json|ya?ml|md|css|html|sql))`'
)

# Repli de parse_agent_file_blocks quand l'agent produit un unique bloc de
# code balisé ``` (markdown standard) au lieu du contrat <<<DEVAIMAZING_FILE.
_FENCED_CODE_PATTERN = re.compile(r'```(?:\w+)?\n(.*?)\n```', re.DOTALL)


def extract_file_paths(text: str) -> list[str]:
    """
    Liste les chemins de fichiers référencés dans un texte (fiche agent).

    Args:
        text: Texte dans lequel chercher des chemins de fichiers.

    Returns:
        Chemins relatifs uniques, triés, détectés entre backticks avec une
        extension de fichier reconnue (ex : `backend/main.py`) — qu'ils
        existent déjà sur disque ou non (contrairement à
        read_referenced_files, qui ne garde que les fichiers existants).

    Example:
        >>> extract_file_paths("Modifier `backend/main.py`.")
        ['backend/main.py']
    """
    return sorted(set(_REFERENCED_FILE_PATTERN.findall(text)))


async def read_card(card_path: Path) -> str:
    """
    Lit une fiche .md.

    Args:
        card_path: Chemin absolu vers la fiche.

    Returns:
        Contenu de la fiche en texte.

    Raises:
        FileNotFoundError: Si la fiche n'existe pas.
    """
    if not card_path.is_file():
        raise FileNotFoundError(f"Fiche introuvable : {card_path}")
    return card_path.read_text(encoding="utf-8")


async def write_card(card_path: Path, content: str) -> None:
    """
    Écrit ou écrase une fiche .md.

    Args:
        card_path: Chemin absolu vers la fiche.
        content: Contenu Markdown à écrire.

    Side effects:
        Crée ou écrase le fichier. Crée les répertoires parents si nécessaire.
    """
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(content, encoding="utf-8")


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


async def read_referenced_files(repo_path: Path, text: str) -> str:
    """
    Lit le contenu des fichiers existants référencés dans un texte (fiche agent).

    Args:
        repo_path: Racine du repo projet cible.
        text: Texte dans lequel chercher des chemins de fichiers (typiquement
            le contenu d'une fiche agent, section "Fichiers à modifier").

    Returns:
        Contenu concaténé des fichiers référencés qui existent réellement sur
        disque, chacun précédé d'un titre indiquant son chemin exact. Chaîne
        vide si aucun chemin référencé n'existe sur disque (ex : run qui ne
        fait que créer des fichiers, rien à modifier).

    Notes:
        Détection par regex sur les chemins entre backticks avec une
        extension de fichier reconnue (ex : `backend/main.py`) — pas un
        parsing strict d'une section markdown dédiée, le format exact des
        fiches variant selon l'agent producteur (voir prompts/pm.md). Un
        chemin référencé qui n'existe pas sur disque est simplement ignoré
        (cas normal : fichier à créer, mentionné dans le même paragraphe
        qu'un fichier à modifier).

        Existe pour combler un gap réel trouvé en run (2026-07-11, voir
        docs/roadmap.md) : sans le contenu actuel du fichier à modifier, un
        agent producteur (Qwen, contexte limité) reconstruit le fichier de
        mémoire au lieu de l'éditer chirurgicalement — imports et handlers
        existants perdus ou remplacés par du code générique non conforme au
        projet.

    Example:
        >>> content = await read_referenced_files(
        ...     Path("/home/user/code/demo"), "Modifier `backend/main.py`."
        ... )
    """
    paths = extract_file_paths(text)
    parts = []
    for relative_path in paths:
        full_path = repo_path / relative_path
        if full_path.is_file():
            content = await read_card(full_path)
            parts.append(f"### Contenu actuel de `{relative_path}`\n\n```\n{content}\n```")
    return "\n\n".join(parts)


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


def parse_agent_file_blocks(text: str, fallback_path: Optional[str] = None) -> dict[str, str]:
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

    Returns:
        Mapping chemin relatif -> contenu du fichier, dans l'ordre
        d'apparition dans `text`. Si plusieurs blocs déclarent le même
        chemin, le dernier l'emporte.

    Raises:
        ValueError: Si `text` ne contient aucun bloc de fichier reconnu, et
            que le repli (fallback_path) ne s'applique pas non plus (absent,
            ou `text` contient zéro ou plusieurs blocs ``` — ambigu, pas de
            devinette dans ce cas).

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
    matches = _FILE_BLOCK_PATTERN.findall(text)
    if matches:
        return {path: content for path, content in matches}

    if fallback_path is not None:
        fenced_blocks = _FENCED_CODE_PATTERN.findall(text)
        if len(fenced_blocks) == 1:
            return {fallback_path: fenced_blocks[0].strip()}

    raise ValueError(
        "Aucun bloc de fichier reconnu dans la sortie de l'agent "
        '(format attendu : <<<DEVAIMAZING_FILE path="...">>> ... <<<DEVAIMAZING_END>>>)'
    )


def parse_structured_file_output(content: str) -> tuple[dict[str, str], str]:
    """
    Parse la sortie structurée d'un agent producteur (Back/Front/Test) appelé
    avec tools.ollama.FILE_OUTPUT_SCHEMA (voir docs/roadmap.md, chantier
    "sortie structurée", 2026-07-11) — remplace parse_agent_file_blocks pour
    ces trois agents (Ollama/Qwen), qui n'utilisent plus le contrat par
    délimiteurs texte <<<DEVAIMAZING_FILE>>>.

    Args:
        content: Sortie JSON brute générée par l'agent (champ "content" du
            retour de tools.ollama.run_ollama, appelé avec
            response_format=FILE_OUTPUT_SCHEMA).

    Returns:
        (files, blocked_reason). `files` : mapping chemin relatif -> contenu
        intégral, vide si l'agent a signalé un blocage. `blocked_reason` :
        raison du blocage signalée par l'agent ; chaîne vide si aucun blocage
        (cas normal).

    Raises:
        ValueError: Si `content` n'est pas un JSON valide, ou si sa structure
            ne correspond pas au schéma attendu (champs "files"/"blocked_reason"
            absents, ou entrée de fichier sans "path"/"content"). Le grammar-
            constrained decoding d'Ollama garantit normalement la conformité,
            mais ce n'est pas supposé sans vérification (voir Notes de
            tools.ollama.run_ollama).

    Example:
        >>> parse_structured_file_output(
        ...     '{"files": [{"path": "backend/a.py", "content": "x = 1"}], '
        ...     '"blocked_reason": ""}'
        ... )
        ({'backend/a.py': 'x = 1'}, '')
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Sortie structurée invalide (JSON attendu) : {exc}") from exc

    if not isinstance(data, dict) or "files" not in data or "blocked_reason" not in data:
        raise ValueError(
            "Sortie structurée incomplète : champs 'files' et 'blocked_reason' "
            f"attendus (voir tools.ollama.FILE_OUTPUT_SCHEMA), reçu : {content!r}"
        )

    files: dict[str, str] = {}
    for entry in data["files"]:
        if not isinstance(entry, dict) or "path" not in entry or "content" not in entry:
            raise ValueError(
                f"Entrée de fichier incomplète (path/content attendus), reçu : {entry!r}"
            )
        files[entry["path"]] = entry["content"]

    return files, data["blocked_reason"]
