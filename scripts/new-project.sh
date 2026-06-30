#!/bin/bash
# new-project.sh - Initialise la structure devaimazing dans un repo projet existant
# Usage : ./scripts/new-project.sh <project-name> <repo-path>

set -euo pipefail

PROJECT_NAME="${1:-}"
REPO_PATH="${2:-}"

if [ -z "$PROJECT_NAME" ] || [ -z "$REPO_PATH" ]; then
    echo "Usage : ./scripts/new-project.sh <project-name> <repo-path>"
    echo "Exemple : ./scripts/new-project.sh webaimazing-v2 ~/code/aimazing/webaimazing-v2"
    exit 1
fi

REPO_PATH=$(eval echo "$REPO_PATH")  # Expansion de ~

if [ ! -d "$REPO_PATH" ]; then
    echo "ERREUR : Le répertoire $REPO_PATH n'existe pas."
    exit 1
fi

echo "=== Initialisation devaimazing dans $REPO_PATH ==="

# Création de la structure specs/ dans le repo projet
mkdir -p "$REPO_PATH/specs"

# Copie des templates
cp templates/project-map.md.template "$REPO_PATH/specs/project-map.md"
cp templates/architect-map.md.template "$REPO_PATH/specs/architect-map.md"

# Remplacement des placeholders dans les templates
sed -i '' "s/{{PROJECT_NAME}}/$PROJECT_NAME/g" "$REPO_PATH/specs/project-map.md"
sed -i '' "s/{{DATE}}/$(date +%Y-%m-%d)/g" "$REPO_PATH/specs/project-map.md"
sed -i '' "s/{{PROJECT_NAME}}/$PROJECT_NAME/g" "$REPO_PATH/specs/architect-map.md"
sed -i '' "s/{{DATE}}/$(date +%Y-%m-%d)/g" "$REPO_PATH/specs/architect-map.md"

# Création du fichier de config projet si inexistant
CONFIG_FILE="config/projects/$PROJECT_NAME.yml"
if [ ! -f "$CONFIG_FILE" ]; then
    cp config/projects/webaimazing-v2.yml "$CONFIG_FILE"
    sed -i '' "s/webaimazing-v2/$PROJECT_NAME/g" "$CONFIG_FILE"
    sed -i '' "s|~/code/aimazing/webaimazing-v2/|$REPO_PATH/|g" "$CONFIG_FILE"
    echo "Fichier de config créé : $CONFIG_FILE"
    echo "Pense à adapter les contraintes projet dans ce fichier."
fi

echo ""
echo "=== Initialisation terminée ==="
echo ""
echo "Structure créée dans $REPO_PATH/specs/ :"
echo "  specs/project-map.md    (carte des fichiers du projet)"
echo "  specs/architect-map.md  (contraintes et patterns architecturaux)"
echo ""
echo "Prochaine étape : devaimazing run $PROJECT_NAME"
echo ""
