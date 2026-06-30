#!/bin/bash
# setup.sh - Installation complète de devaimazing
# Usage : ./scripts/setup.sh

set -euo pipefail

echo "=== devaimazing setup ==="

# Vérifications préalables
echo "Vérification des prérequis..."

if ! command -v uv &> /dev/null; then
    echo "ERREUR : uv n'est pas installé. https://github.com/astral-sh/uv"
    exit 1
fi

if ! command -v ollama &> /dev/null; then
    echo "ERREUR : Ollama n'est pas installé. https://ollama.ai/"
    exit 1
fi

if ! command -v claude &> /dev/null; then
    echo "ERREUR : Claude Code CLI n'est pas installé."
    echo "         Installer depuis : https://claude.ai/code"
    exit 1
fi

if ! command -v git &> /dev/null; then
    echo "ERREUR : Git n'est pas installé."
    exit 1
fi

echo "Prérequis OK."

# Création des répertoires de données
echo "Création des répertoires ~/.devaimazing/..."
mkdir -p ~/.devaimazing

# Installation des dépendances Python
echo "Installation des dépendances Python avec uv..."
uv sync
uv pip install -e .

echo "devaimazing installé. Vérification de l'installation..."
devaimazing --version

# Pull du modèle Ollama
echo "Pull du modèle Ollama (qwen2.5:7b-instruct)..."
ollama pull qwen2.5:7b-instruct

echo ""
echo "=== Installation terminée ==="
echo ""
echo "Prochaines étapes :"
echo "  1. Configurer ton projet : cp config/projects/webaimazing-v2.yml config/projects/mon-projet.yml"
echo "  2. Vérifier l'environnement : devaimazing doctor"
echo "  3. Tester sur l'exemple : devaimazing run demo-todo-app"
echo ""
