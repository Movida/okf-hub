# Patches pour les livrables distincts

Le template de bundle (`okf-bundle-template`) est un dépôt à part : une
modification qui lui revient ne s'applique pas d'ici. Elle est livrée en patch,
appliquée en amont, puis **retirée de ce répertoire** (`ARCHITECTURE.md` § 9).

## `okf-bundle-template-rev-4.5.patch`

Spec rév. 4.5 (§ 3.3, § 3.5, § 9) : le profil de base et `cites`. Ne change
aucun comportement du template — tout est en commentaire, à décommenter par la
base qui s'en sert.

| Fichier | Ajout |
|---|---|
| `okf-bundle.yaml` | `cites`, commenté |
| `schema.yaml` | Le profil, commenté : rôles de champ, `types`, `facets`, `sections`, `views` |
| `INSTANTIATE.md` | Étape 6 : la question du profil et de `cites` |

Application, depuis le clone du template, avec son identité git :

```sh
git apply --check <hub>/docs/patches/okf-bundle-template-rev-4.5.patch
git apply <hub>/docs/patches/okf-bundle-template-rev-4.5.patch
git commit -am "Profil de base et cites (spec hub rév. 4.5)"
```

Puis retirer le patch et cette section d'ici, en citant le commit amont.
