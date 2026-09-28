<p align="center">
  <img src="app/icon/Boulito.iconset/icon_256x256.png" width="128" alt="Boulito">
</p>

<h1 align="center">Boulito</h1>

<p align="center"><b>Un assistant vocal pour macOS qui tourne à 100 % sur votre Mac.</b><br>
Parlez à votre Mac en français ou en anglais : apps, YouTube dans Safari, volume, musique, minuteurs, rappels, agenda, messages, dictée et questions rapides.<br>
Pas de cloud, pas de compte, pas de serveur, aucun port ouvert.</p>

<p align="center"><a href="README.md">English version</a> · <a href="docs/conception.fr.md">Notes de conception</a></p>

<p align="center"><a href="https://github.com/Bouliw/boulito/actions/workflows/tests.yml"><img src="https://github.com/Bouliw/boulito/actions/workflows/tests.yml/badge.svg" alt="Tests"></a></p>

---

Boulito vit dans la barre des menus. Maintenez une touche (⌥ droite par défaut) et parlez, ou dites simplement son nom. La reconnaissance vocale ([Parakeet](https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v3)), le modèle d'IA ([Qwen 3.5](https://huggingface.co/mlx-community/Qwen3.5-9B-MLX-4bit), via [MLX](https://github.com/ml-explore/mlx) d'Apple) et toutes les actions tournent sur votre Mac. Les commandes simples passent par des règles rapides, en 0,2 s environ ; le modèle d'IA n'intervient que pour les demandes libres (1 s environ).

## Ce que vous pouvez dire

| | Exemples |
|---|---|
| **Apps** | « Ouvre Notes », « Ferme Spotify », « Passe sur Safari », « Nouvel onglet dans Safari » |
| **YouTube (Safari)** | « Cherche des documentaires sur les volcans de cette semaine », « Lance la deuxième », « Mets celle qui parle de l'Islande », « Passe la pub », « Recule de 30 secondes », « Chapitre suivant », « En x1,5 », « Sous-titres en français », « Plein écran », « Like », « Abonne-toi » *(vous demande d'abord)* |
| **Résumés** | « Résume cette vidéo », « De quoi il parle à 10 minutes ? » |
| **Mac** | « Monte le son », « Volume à 30 % », « Coupe le son », « Verrouille le Mac », « Ouvre lemonde.fr » |
| **Écrire** | « Écris : acheter du pain », « Ouvre Notes puis écris appeler la banque », « Dicte » *(dictée longue)*, « à la ligne », « nouveau paragraphe » |
| **Messages** | « Envoie un message à Paul sur Discord pour lui dire que j'arrive » (Discord, Messages, WhatsApp, Telegram) *(relu, envoyé seulement après « oui »)* |
| **Temps** | « Minuteur 10 minutes », « Il reste combien ? », « Rappelle-moi demain à 9 h d'appeler la banque » *(vous demande d'abord)*, « Qu'est-ce que j'ai demain ? », « Ajoute un rendez-vous lundi à 14 h 30 avec Paul » *(vous demande d'abord)* |
| **Musique** | « Mets l'album Discovery », « Joue ma playlist sport », « C'est quoi cette chanson ? » (votre bibliothèque Apple Music) |
| **Questions** | « Comment on dit facture en anglais ? », « C'est quoi un ETF ? », « Combien font 15 % de 80 ? », « Combien de kilomètres font 10 miles ? » |

L'interface parle français, anglais, espagnol, allemand, italien ou portugais. Au premier lancement, Boulito prend la langue de votre Mac et la comprend, ainsi que l'anglais ; les deux se changent à tout moment dans la configuration (Langue, Langues comprises).

## Deux façons de parler

- **Touche maintenue** (par défaut) : le micro n'est ouvert que pendant l'appui. Choisissez n'importe quelle touche ou combinaison dans la fenêtre de configuration.
- **Écoute ouverte** : dites « Boulito, mets pause » (ou le nom que vous avez choisi). Comme Siri, il entend son nom même pendant une vidéo sur les haut-parleurs : l'annulation d'écho de macOS retire du micro le son du Mac, le nom est cherché en continu, et le son du Mac baisse le temps de la commande (« Baisser le son pendant l'écoute », que vous pouvez décocher). Le **mode conversation** écoute encore quelques secondes après une commande (5 par défaut, `conversation_s` dans `config.toml`), pour enchaîner des commandes simples (« monte le son », « vidéo suivante », « encore », « encore un peu ») sans redire le nom.

## Vie privée et sécurité

- **Tout reste sur votre Mac.** Aucun serveur ne tourne, même sur `127.0.0.1` : le modèle d'IA tourne dans le processus de Boulito. Le réseau ne sert qu'aux téléchargements : reconnaissance vocale et modèles d'IA quand vous cliquez sur **Télécharger** dans la fenêtre de configuration ou choisissez un modèle absent dans le menu **Modèle**, paquets Python par `install.sh` dans la version source, et, si vous installez le raccourci du minuteur, à macOS pour le signer. L'app du `.dmg` demande aussi à GitHub, une fois par jour, si une nouvelle version est sortie (sans compte, rien sur vous n'est envoyé ; désactivable dans la fenêtre de configuration). `./voix check --network` charge la reconnaissance vocale et le modèle d'IA choisi dans `config.toml`, puis vérifie qu'aucune connexion ne sort et qu'aucun port n'est ouvert.
- **Rien n'est enregistré.** L'audio n'est jamais sauvegardé. En écoute ouverte, seules les 2 dernières secondes restent en mémoire, et elles sont jetées si le nom n'y est pas. Le journal local (`logs/`, dans `~/Library/Application Support/Boulito` pour l'app) garde les commandes adressées à Boulito et ce qui a été fait, 7 jours ; jamais l'audio.
- **Une liste blanche, pas un terminal.** L'IA ne peut appeler qu'une liste fixe d'outils, chacun revérifié avant d'agir. Elle ne peut jamais lancer une commande, toucher à vos fichiers ni changer vos réglages.
- **Les pages web ne sont pas fiables.** Les titres et descriptions YouTube sont écrits par des inconnus : ils sont donnés au modèle comme des données délimitées, auxquelles il ne doit jamais obéir, et les outils sensibles sont protégés de toute façon.
- **Vous confirmez ce qui compte.** S'abonner, commenter, envoyer un message, créer un rappel ou un événement : Boulito relit et attend un « oui » clair. « Non », un silence, une hésitation ou le moindre doute annulent.
- **Vos mots, pas ceux de l'IA.** Le texte tapé, les messages, les commentaires et les rappels sont exactement ce que vous avez dicté, jamais un texte écrit par l'IA ou tiré d'une page. Boulito n'appuie jamais sur Entrée pour envoyer, et ne tape jamais dans un terminal ni dans un champ de mot de passe.
- **Les permissions sont celles de Boulito.app**, pas de Terminal. Le JavaScript n'est injecté que dans les onglets youtube.com.

### Modèle de sécurité

Boulito.app détient des permissions puissantes : Accessibilité (taper et appuyer sur des touches dans les autres apps), Surveillance de la saisie, micro et Automatisation (piloter Safari, Musique et Rappels). Tout code qui tourne dans l'app en profite aussi : n'exécutez que le code de ce dépôt, ou d'une copie que vous avez relue et en laquelle vous avez confiance. Le réglage *Autoriser JavaScript depuis les Apple Events* de Safari permet à toute app autorisée à piloter Safari d'exécuter du JavaScript dans vos onglets, y compris ceux où vous êtes connecté : désactivez-le si vous n'utilisez plus Boulito. L'app du `.dmg` est signée avec le certificat propre à Boulito, sans validation d'Apple (il faudrait un compte payant) : téléchargez-la seulement depuis la page [Releases](https://github.com/Bouliw/boulito/releases) de ce dépôt. Sa mise à jour n'installe une nouvelle version que si son empreinte SHA-256 est celle publiée par GitHub et qu'elle est signée par ce même certificat. En écoute ouverte, n'importe qui près de votre Mac, ou une vidéo, peut dire le mot d'activation : si vous vous en servez dans un lieu public, remplacez le nom par défaut par un nom à vous (fenêtre de configuration → Changer…).

## Prérequis

- Un Mac avec puce Apple (M1 ou plus récent) et macOS 14 ou plus récent. Développé et mesuré sur un M4 Max sous macOS 26.
- La mémoire recommandée dépend du modèle d'IA choisi : 8 Go pour Rapide, 16 Go pour Élevé (par défaut), 24 Go pour Extrême, 32 Go pour Ultraboost. Ce n'est qu'un conseil : rien n'est bloqué. Environ 8 Go d'espace disque avec le modèle Élevé (reconnaissance vocale 2,3 Go, modèle d'IA 5,6 Go).
- Safari, pour les fonctions YouTube.
- Version source seulement : [Homebrew](https://brew.sh) avec `uv` (`brew install uv`), Python 3.13 (`brew install python@3.13`) et les outils en ligne de commande d'Apple (`xcode-select --install`). L'app n'a besoin d'aucun d'eux.

`./voix check` ne bloque jamais sur la mémoire : il donne seulement un conseil, pour le modèle choisi dans `config.toml`. Il échoue quand il manque quelque chose (Python 3.13, uv, reconnaissance vocale) ou quand l'espace disque ne suffit pas.

## Installation

### Télécharger l'app (le plus simple)

1. Téléchargez le fichier `.dmg` de la [dernière version](https://github.com/Bouliw/boulito/releases/latest) (environ 280 Mo), ouvrez-le et glissez **Boulito** dans **Applications**.
2. Ouvrez Boulito. La première fois, macOS dit qu'il ne peut pas vérifier l'absence de logiciel malveillant : Boulito est gratuit et n'est pas signé avec un compte développeur Apple payant. Fermez ce message, ouvrez **Réglages Système → Confidentialité et sécurité**, descendez jusqu'à *« Boulito » a été bloqué* et cliquez sur **Ouvrir quand même** (votre mot de passe ou Touch ID est demandé). Une seule fois par version.
3. La fenêtre de configuration s'ouvre : cliquez sur **Télécharger** à côté de **Reconnaissance vocale** (2,5 Go, une seule fois). Boulito écoute dès qu'elle est installée ; choisissez ensuite votre modèle d'IA.

L'app contient son propre Python : ni Homebrew, ni Terminal. Ses réglages, modèles et journaux sont rangés dans `~/Library/Application Support/Boulito`.

### Depuis les sources

```sh
git clone https://github.com/Bouliw/boulito.git
cd boulito
./install.sh     # environnement Python, reconnaissance vocale (~2,3 Go) et l'app Boulito.app
./boulito        # lance Boulito (ou double-cliquez sur Boulito.app)
```

Tout reste dans le dossier du projet. `./app/package.sh` fabrique le `.dmg` à partir du dernier commit.

### Premier lancement

`install.sh` ne télécharge que la reconnaissance vocale, et l'app rien du tout : le modèle d'IA se choisit ensuite. Au premier lancement, la fenêtre de configuration vous guide. **Choisissez-y votre modèle d'IA** : chacun indique sa taille et la mémoire qu'il demande, celui qui convient à votre Mac est marqué *recommandé*, et un bouton **Télécharger** l'installe en arrière-plan (progression affichée), puis Boulito l'utilise. Les commandes simples marchent déjà pendant le téléchargement. La fenêtre liste aussi les principales permissions avec leur état en direct, et un bouton qui ouvre le bon panneau des Réglages Système ; macOS demande Musique et Rappels à la première utilisation :

| Permission | Pourquoi |
|---|---|
| Micro | Pour vous entendre (seulement pendant l'appui, ou en continu en écoute ouverte) |
| Surveillance de la saisie | Pour repérer la touche de parole et Échap. Les frappes ne sont ni lues ni gardées |
| Accessibilité *(facultatif)* | Pour taper ce que vous dictez, utiliser les raccourcis standard (⌘N, ⌘T, ⌘W, ⌘F) et les touches média, envoyer des messages et verrouiller l'écran ; aussi pour qu'une touche de parole combinée à une lettre ne s'écrive pas dans l'app. Jamais dans un terminal ni dans un champ de mot de passe |
| Automatisation → Safari | Pour piloter YouTube. Activez aussi Safari → Réglages → Développeur → *Autoriser JavaScript depuis les Apple Events* |
| Automatisation → Musique | Pour lire votre bibliothèque Apple Music et dire ce qui joue (demandée à la première utilisation) |
| Automatisation → Rappels | Pour créer et lire des rappels, et les minuteurs de 24 heures ou plus (demandée à la première utilisation) |
| Calendrier *(facultatif)* | Pour lire votre agenda et ajouter des événements, toujours après votre « oui » |
| Notifications | Pour afficher ce qui a été compris et fait. macOS pose la question au premier lancement ; s'il ne l'a pas fait, ou si elles ont été refusées, le bouton **Ouvrir** de la configuration mène directement à Réglages Système → Notifications → Boulito : activez *Autoriser les notifications* |

`install.sh` propose de créer un certificat de signature sur votre Mac (`./app/signing.sh trust`, votre mot de passe est demandé une fois) : Boulito.app garde alors ses permissions quand elle est reconstruite.

**Minuteurs dans l'app Horloge** *(facultatif)* : macOS ne permet de les lancer que par Raccourcis. Dans la fenêtre de configuration, **Installer** signe sur votre Mac les deux raccourcis du minuteur livrés dans `shortcuts/`, puis les ouvre : cliquez sur *Ajouter le raccourci*. Sans eux, Boulito garde ses propres minuteurs.

## Modèles d'IA

Téléchargez et changez de modèle depuis la fenêtre de configuration (ou le menu **Modèle**), en un clic, sans ligne de commande. La mémoire recommandée n'est qu'une indication : rien n'est bloqué.

| Niveau | Modèle | Disque | Mémoire recommandée | Temps médian |
|---|---|---|---|---|
| Rapide | Qwen 3.5 4B (4 bits) | 2,9 Go | 8 Go | 0,6 s |
| **Élevé** (défaut) | Qwen 3.5 9B (4 bits) | 5,6 Go | 16 Go | 1,1 s |
| Extrême | Qwen 3.5 35B-A3B allégé (REAP 19B) | 11,5 Go | 24 Go | 0,7 s |
| Ultraboost | Qwen 3.5 35B-A3B | 20,4 Go | 32 Go | 0,5 s |

Mesuré sur un M4 Max avec `./voix test`, qui rejoue le jeu de test sans micro ni Safari. Le modèle Élevé a réussi 99 à 100 % des tests de bout en bout.

## Réglages

Le menu contient ce qu'on change souvent (modèle, mode d'écoute, touche de parole, langues, son baissé pendant l'écoute, mode conversation). La fenêtre de configuration (**Configuration…**) contient le reste : permissions, modèles d'IA, nom et mot d'activation, options de l'écoute ouverte, annonces vocales, bips, notifications, icône de la barre des menus, lancement au démarrage, raccourci du minuteur. Tout est enregistré dans `config.toml`, créé à partir de [`config.example.toml`](config.example.toml) au premier lancement.

## Ligne de commande

| Commande | Rôle |
|---|---|
| `./boulito` / `./boulito stop` | Lance ou arrête Boulito.app |
| `./boulito diag` | Montre les permissions de Boulito.app, et ce qu'elle voit de l'app au premier plan |
| `./boulito diag --notification` | Envoie une notification d'essai depuis Boulito.app |
| `./voix check` | Vérifie le Mac, les modèles, les permissions de l'app qui le lance, et que tous les caches restent dans le dossier du projet ; la mémoire n'est qu'un conseil |
| `./voix check --network` | Pareil, plus 15 s de surveillance du réseau avec la reconnaissance vocale et le modèle d'IA choisi chargés |
| `./voix stats` | Réussite et latence de vos commandes vocales, d'après le journal local (dernières 24 h ; `--hours 168` pour 7 jours) |
| `./voix run "lance la deuxième"` | Exécute une phrase écrite, comme si elle avait été dite (depuis Terminal : macOS demande alors ses propres autorisations à Terminal, par exemple pour piloter Safari) |
| `./voix route "monte le son"` | Montre ce qu'une phrase ferait, sans rien faire |
| `./voix route --test` | Rejoue les tests des règles : phrases, durées, dates, confirmations, dictée, calculs |
| `./voix test` | Rejoue les tests de bout en bout avec le modèle d'IA, sur des pages YouTube simulées |
| `./voix download --model 35b` | Télécharge un autre modèle d'IA |

## Mise à jour

**App** : quand une nouvelle version sort, Boulito affiche une notification. Cliquez sur **Mettre à jour** dans le menu ou la fenêtre de configuration : il télécharge la nouvelle version, la vérifie, se remplace et redémarre. Réglages, modèles et permissions sont gardés. Vous pouvez aussi télécharger le nouveau `.dmg` et remplacer Boulito dans Applications à la main. (Depuis la 0.2.0 : téléchargez une fois la 0.2.1 à la main, et accordez de nouveau les permissions.)

**Depuis les sources** :

```sh
git pull && ./install.sh
```

`install.sh` met à jour l'environnement Python, garde les modèles déjà téléchargés et reconstruit Boulito.app. Avec le certificat de signature local (`./app/signing.sh trust`), Boulito.app garde ses permissions ; sans lui, macOS peut les redemander.

Laissez le dossier du projet où il est : le déplacer casse l'environnement Python (`.venv`), le lancement au démarrage et les permissions accordées à Boulito.app. Si vous le déplacez quand même : supprimez `.venv`, relancez `./install.sh`, décochez puis recochez *Lancer au démarrage du Mac* dans la fenêtre de configuration, et accordez de nouveau les permissions.

## Dépannage

- **YouTube ne réagit pas, ou une erreur parle de JavaScript** : dans Safari, cochez Réglages → Avancés → *Afficher les fonctionnalités pour les développeurs web*, puis Réglages → Développeur → *Autoriser JavaScript depuis les Apple Events*.
- **Une fonction ne marche pas (écrire, touche de parole, agenda…)** : ouvrez **Configuration…** depuis le menu : chaque permission affiche ✅ ou ❌ (⚪️ quand macOS ne peut pas le dire, comme pour le réglage de Safari), avec un bouton qui ouvre le bon panneau des Réglages Système. Après avoir autorisé la Surveillance de la saisie, relancez Boulito.
- **Pas de notifications** : elles ont sans doute été refusées. Dans la fenêtre de configuration, le bouton **Ouvrir** à côté de Notifications mène à Réglages Système → Notifications → Boulito : activez *Autoriser les notifications*. `./boulito diag --notification` en envoie une pour essayer.
- **Autre chose** : la sortie de Boulito est dans `logs/AAAA-MM-JJ.app.log`, gardée 7 jours (dans `~/Library/Application Support/Boulito` pour l'app, dans le dossier du projet pour la version source), et `./voix check` liste ce qui manque.

## Désinstallation

**App** : dans la fenêtre de configuration, **Tout effacer…** supprime réglages, modèles et journaux, retire le lancement au démarrage et ferme Boulito. Glissez ensuite Boulito d'Applications à la corbeille. À la main, si vous le souhaitez : retirez ses permissions dans Réglages Système → Confidentialité et sécurité, décochez *Autoriser JavaScript depuis les Apple Events* dans Safari, supprimez les raccourcis du minuteur dans Raccourcis et la playlist « Boulito queue » dans Musique.

**Depuis les sources** :

1. `./uninstall.sh` : arrête Boulito, retire le lancement au démarrage et la confiance du certificat de signature local s'ils existent, et liste ce qu'il reste à défaire à la main (permissions, réglage de Safari, raccourcis du minuteur, playlist « Boulito queue »).
2. Mettez le dossier du projet à la corbeille. Modèles, caches, journaux et réglages sont tous dedans.

## Comment ça marche

Touche ou mot d'activation → micro (16 kHz, annulation d'écho en écoute ouverte) → Silero VAD → Parakeet → **règles** (expressions régulières, français et anglais, moins de 3 ms) → sinon **Qwen 3.5** avec appel d'outils et un état texte de la page YouTube → **exécuteur** (liste blanche, contrôles, confirmations) → Safari (JavaScript dans l'onglet YouTube), `open -a`, AppleScript ou Raccourcis → son, notification et réponse vocale.

Les choix techniques, les mesures et les limites sont détaillés dans les [notes de conception](docs/conception.fr.md). Tous les sélecteurs de YouTube sont en tête de [`src/voix/youtube.js`](src/voix/youtube.js), pour être réparés vite quand YouTube change son interface ; tous les textes de l'interface sont dans [`src/voix/i18n.py`](src/voix/i18n.py), une colonne par langue.

## Limites

- Le pilotage de YouTube dépend de son interface web : une refonte peut casser une action jusqu'à la mise à jour de son sélecteur.
- YouTube seulement dans Safari, et seulement votre bibliothèque Apple Music (pas tout le catalogue).
- Boulito ne clique pas dans l'interface des autres apps (trop risqué : les boutons Envoyer, Supprimer ou Payer sont à un clic) ; il utilise des recettes ciblées (messageries) et les raccourcis standard.
- Le modèle d'IA fonctionne hors ligne : il ne peut pas connaître l'actualité, la météo ni les prix, et ses connaissances s'arrêtent à la date de son entraînement.

## Licence

[GPL-3.0](LICENSE). Copyright © 2026 Bouliw.

Boulito est un projet indépendant, sans lien avec Apple, Google ou YouTube. Les modèles gardent leurs propres licences (Qwen 3.5 : Apache 2.0 ; Parakeet : CC BY 4.0 ; Silero VAD : MIT).
