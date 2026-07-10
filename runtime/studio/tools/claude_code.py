"""
Wrapper subprocess pour Claude Code CLI.

Lance Claude Code en sous-process avec le prompt et le modèle spécifiés.
Capture la sortie, parse le JSON, retourne le résultat.
"""

import asyncio
import json
from pathlib import Path
from typing import Optional


async def run_claude_code(
    prompt: str,
    model: str,
    cwd: Path,
    timeout_seconds: int = 300,
    output_format: str = "json",
) -> dict:
    """
    Lance Claude Code CLI en sous-process.

    Args:
        prompt: Prompt à envoyer à Claude Code. Transmis via stdin (pas en
            argument de ligne de commande) pour éviter les limites de taille
            d'argv sur les fiches/skills volumineux.
        model: Identifiant du modèle (ex: claude-opus-4-8).
        cwd: Répertoire de travail (repo projet).
        timeout_seconds: Timeout en secondes avant abandon.
        output_format: Format de sortie (json recommandé pour parsing — les
            autres valeurs ("text", "stream-json") ne sont pas parsables par
            cette fonction et déclenchent un ValueError).

    Returns:
        Dictionnaire avec les champs : content, usage (tokens), duration_ms.

    Raises:
        TimeoutError: Si le sous-process dépasse timeout_seconds.
        RuntimeError: Si Claude Code retourne un code d'erreur non nul, si la
            sortie JSON contient un champ "is_error" à true, ou si
            "permission_denials" est non vide (voir Notes).
        ValueError: Si la sortie JSON est invalide.

    Example:
        >>> result = await run_claude_code(
        ...     prompt="Écris la fiche racine pour : ajouter un endpoint de login",
        ...     model="claude-opus-4-8",
        ...     cwd=Path("/home/user/code/aimazing/webaimazing-v2"),
        ... )
        >>> print(result["content"])

    Notes:
        Aucun flag de permissions (--dangerously-skip-permissions,
        --allowedTools) n'est ajouté — vérifié empiriquement (2026-07-10,
        invocations réelles) que ce n'est pas nécessaire pour l'usage actuel
        des agents devaimazing : en mode -p non interactif, les outils en
        lecture seule (Read, Glob, Grep) sont exécutés sans invite, alors
        qu'un outil de mutation (Write, Edit, Bash) est refusé proprement
        (is_error reste false, le process se termine normalement, aucun
        hang) plutôt que de bloquer sur une invite. Cohérent avec le design
        des agents qui appellent ce wrapper (architect, security, pm) :
        ils lisent le repo cible via les outils de Claude Code, mais toute
        écriture de fichier passe par tools.filesystem.write_card côté
        devaimazing, jamais par un outil Write de Claude Code lui-même. Si
        un futur agent a besoin d'écrire directement via Claude Code, ce
        point sera à retrancher explicitement (ajouter --allowedTools plutôt
        que --dangerously-skip-permissions, qui n'est de toute façon
        recommandé que pour un sandbox sans accès réseau).
    """
    process = await asyncio.create_subprocess_exec(
        "claude", "-p", "--model", model, "--output-format", output_format,
        cwd=str(cwd),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=prompt.encode("utf-8")),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise TimeoutError(
            f"Claude Code CLI a dépassé le délai imparti ({timeout_seconds}s) "
            f"pour le modèle {model!r}"
        ) from exc

    if process.returncode != 0:
        raise RuntimeError(
            f"Claude Code CLI a échoué (code {process.returncode}) : "
            f"{stderr.decode('utf-8', errors='replace').strip()}"
        )

    raw_output = stdout.decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Sortie JSON invalide de Claude Code CLI : {exc}") from exc

    if payload.get("is_error"):
        raise RuntimeError(
            f"Claude Code CLI a retourné une erreur : {payload.get('result') or payload}"
        )

    denials = payload.get("permission_denials") or []
    if denials:
        denied_tools = ", ".join(sorted({d.get("tool_name", "?") for d in denials}))
        raise RuntimeError(
            f"Claude Code CLI s'est vu refuser l'accès à un outil ({denied_tools}) — "
            f"le design actuel des agents devaimazing ne doit jamais avoir besoin "
            f"d'écrire via Claude Code (voir Notes de run_claude_code)."
        )

    return {
        "content": payload.get("result", ""),
        "usage": payload.get("usage", {}),
        "duration_ms": payload.get("duration_ms", 0),
    }
