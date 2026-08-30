## Ce que change cette PR

## Pourquoi

<!-- Si cela modifie un comportement fixé par la spécification, citez le
     paragraphe et expliquez. Un écart doit être discuté en issue AVANT
     d'être codé, et documenté dans docs/ARCHITECTURE.md § 5. -->

## Vérifications

- [ ] `uv run pytest -q` passe en entier (pas seulement `-m "not slow"`)
- [ ] Aucun test de concurrence n'a été affaibli pour faire passer le changement
- [ ] Tout YAML produit passe par `yaml.safe_dump` (§ 1.7)
- [ ] Si un outil MCP change : `docs/API.md` et le tableau du README sont à jour
- [ ] Si un écart à la spec est introduit : il figure dans `docs/ARCHITECTURE.md` § 5
