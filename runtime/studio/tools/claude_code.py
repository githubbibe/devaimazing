"""
Wrapper subprocess pour Claude Code CLI.

Lance Claude Code en sous-process avec le prompt et le modèle spécifiés.
Capture la sortie, parse le JSON, retourne le résultat.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from studio.tools.tracer import AgentTracer

_logger = logging.getLogger(__name__)


async def run_claude_code(
    prompt: str,
    model: str,
    cwd: Path,
    timeout_seconds: int = 300,
    output_format: str = "json",
    response_schema: Optional[dict] = None,
    tracer: Optional[AgentTracer] = None,
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
        response_schema: JSON Schema optionnel, transmis via `--json-schema`
            (vérifié disponible en mode -p non interactif, voir
            docs/roadmap.md, chantier "sortie structurée" 2026-07-11). Ajoute
            un canal structuré parallèle au texte prose de `content` — ne
            remplace pas le contrat de sortie textuel existant. `None` (par
            défaut) : pas de flag ajouté, comportement inchangé.
        tracer: AgentTracer optionnel (voir tools.tracer) — émet
            llm_call_start/llm_call_end autour de l'appel, warning sur un
            refus d'outil non fatal, error sur timeout/échec/refus fatal.
            `None` (défaut) : aucune trace émise, comportement inchangé.

    Returns:
        Dictionnaire avec les champs : content, usage (tokens), duration_ms,
        structured_output (dict conforme à response_schema si fourni et
        renvoyé par le CLI, sinon None — la conformité au schéma n'est pas
        garantie à 100%, voir Notes).

    Raises:
        TimeoutError: Si le sous-process dépasse timeout_seconds.
        RuntimeError: Si Claude Code retourne un code d'erreur non nul, si la
            sortie JSON contient un champ "is_error" à true, ou si
            "permission_denials" est non vide ET qu'aucun contenu exploitable
            n'a été produit (voir Notes — un refus d'outil isolé, suivi d'une
            réponse texte exploitable, n'est plus fatal).
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

        Un refus d'outil (permission_denials non vide) n'est fatal que si le
        modèle n'a produit aucun contenu exploitable après coup — constaté en
        run réel (2026-07-11, voir docs/roadmap.md) qu'un modèle qui tente un
        outil refusé (variance d'échantillonnage, prompt pourtant correct)
        s'en remet ensuite normalement et produit quand même une réponse
        texte valide dans la même invocation. Faire échouer tout le run pour
        un refus récupéré était trop strict. Le refus reste tracé (logger
        warning) même quand il n'est pas fatal, pour garder un signal si un
        prompt donné dérive vers ce comportement de façon récurrente.
    """
    command = ["claude", "-p", "--model", model, "--output-format", output_format]
    if response_schema is not None:
        command += ["--json-schema", json.dumps(response_schema)]

    if tracer is not None:
        tracer.emit("llm_call_start", backend="claude_code", model=model, prompt_chars=len(prompt))

    process = await asyncio.create_subprocess_exec(
        *command,
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
        if tracer is not None:
            tracer.emit(
                "error", backend="claude_code", model=model,
                message=f"timeout après {timeout_seconds}s",
            )
        raise TimeoutError(
            f"Claude Code CLI a dépassé le délai imparti ({timeout_seconds}s) "
            f"pour le modèle {model!r}"
        ) from exc

    if process.returncode != 0:
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        detail = stderr_text or stdout_text or "(stdout et stderr vides)"
        message = f"Claude Code CLI a échoué (code {process.returncode}) : {detail}"
        if tracer is not None:
            tracer.emit("error", backend="claude_code", model=model, message=message)
        raise RuntimeError(message)

    raw_output = stdout.decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        if tracer is not None:
            tracer.emit(
                "error", backend="claude_code", model=model,
                message=f"sortie JSON invalide : {exc}",
                raw_output_head=raw_output[:500],
            )
        raise ValueError(f"Sortie JSON invalide de Claude Code CLI : {exc}") from exc

    if payload.get("is_error"):
        message = f"Claude Code CLI a retourné une erreur : {payload.get('result') or payload}"
        if tracer is not None:
            tracer.emit("error", backend="claude_code", model=model, message=message)
        raise RuntimeError(message)

    content = payload.get("result", "")
    denials = payload.get("permission_denials") or []
    if denials:
        denied_tools = ", ".join(sorted({d.get("tool_name", "?") for d in denials}))
        if not content.strip():
            message = (
                f"Claude Code CLI s'est vu refuser l'accès à un outil ({denied_tools}) "
                f"et n'a produit aucun contenu exploitable — le design actuel des "
                f"agents devaimazing ne doit jamais avoir besoin d'écrire via Claude "
                f"Code (voir Notes de run_claude_code)."
            )
            if tracer is not None:
                tracer.emit("error", backend="claude_code", model=model, message=message)
            raise RuntimeError(message)
        _logger.warning(
            "Claude Code CLI a tenté d'utiliser un outil refusé (%s) mais a quand "
            "même produit un contenu exploitable — refus non fatal (voir Notes de "
            "run_claude_code).",
            denied_tools,
        )
        if tracer is not None:
            tracer.emit(
                "warning", backend="claude_code", model=model,
                message=f"outil(s) refusé(s) mais contenu exploitable : {denied_tools}",
            )

    if tracer is not None:
        usage = payload.get("usage", {})
        tracer.emit(
            "llm_call_end", backend="claude_code", model=model,
            tokens_prompt=usage.get("input_tokens", 0),
            tokens_completion=usage.get("output_tokens", 0),
            duration_ms=payload.get("duration_ms", 0),
        )

    return {
        "content": content,
        "usage": payload.get("usage", {}),
        "duration_ms": payload.get("duration_ms", 0),
        "structured_output": payload.get("structured_output"),
    }
