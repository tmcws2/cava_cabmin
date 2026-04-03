# 📢 Ça Va Le Cabinet ?

Bot de suivi automatique des nominations et mouvements de collaborateurs de cabinets ministériels, publié via le Journal Officiel.

## Canaux de publication

- **Bluesky** : [@votre-handle.bsky.social](https://bsky.app)
- **Telegram** : [Lien vers le canal]

## Source de données

Journal Officiel de la République Française (JORF) via l'[API PISTE de la DILA](https://piste.gouv.fr/).

Chaque nomination, reconduction ou fin de fonctions en cabinet ministériel fait l'objet d'un arrêté publié au JO — c'est cette publication officielle qui déclenche le bot.

## Fonctionnement

1. **Deux fois par jour** (8h et 14h), GitHub Actions lance `main.py`
2. Le script s'authentifie sur l'API PISTE avec des credentials OAuth2
3. Il recherche les nouveaux arrêtés JORF contenant des mots-clés (directeur de cabinet, conseiller de cabinet…)
4. Pour chaque arrêté inédit, il extrait : personne nommée, poste, ministère, type de mouvement
5. Il publie sur **Bluesky** et **Telegram**
6. Il enregistre les IDs traités dans `seen_ids.json` (commité dans le repo)

## Mise en place

### 1. API PISTE

Créer un compte sur [piste.gouv.fr](https://piste.gouv.fr) et souscrire à l'API Légifrance.
Récupérer le `client_id` et le `client_secret`.

### 2. Bot Telegram

1. Ouvrir [@BotFather](https://t.me/botfather) sur Telegram
2. Créer un nouveau bot : `/newbot`
3. Récupérer le token
4. Créer un canal public et ajouter le bot comme administrateur
5. Récupérer le `chat_id` du canal (ex : `@moncanal` ou un ID numérique)

### 3. Secrets GitHub

Dans Settings > Secrets and variables > Actions :

| Secret | Valeur |
|---|---|
| `PISTE_CLIENT_ID` | ID OAuth2 PISTE |
| `PISTE_CLIENT_SECRET` | Secret OAuth2 PISTE |
| `BLUESKY_HANDLE` | Ex: `cavcabinet.bsky.social` |
| `BLUESKY_PASSWORD` | Mot de passe app Bluesky |
| `TELEGRAM_BOT_TOKEN` | Token du bot Telegram |
| `TELEGRAM_CHAT_ID` | ID ou @username du canal |

### 4. Activer le workflow

GitHub désactive automatiquement les workflows inactifs après 60 jours.
Pour les réactiver : Actions > sélectionner le workflow > "Enable workflow".

## Format des posts

```
🟢 NOMINATION EN CABINET
👤 Mme Prénom NOM
🏛️ Directrice de cabinet
🔹 Cabinet : du ministre de l'Économie
📅 JO du 1 avril 2025
🔗 https://www.legifrance.gouv.fr/jorf/id/JORFTEXT...
```

Codes couleur :
- 🟢 Nomination
- 🔴 Fin de fonctions
- 🔵 Renouvellement
- ⚪ Autre mouvement
