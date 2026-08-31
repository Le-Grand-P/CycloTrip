# CycloTrip — proto (nom provisoire)

Première ébauche : un fichier HTML unique, sans build ni backend, même
architecture technique que EasyBiv (Leaflet + turf.js + leaflet-gpx, tout
tourne dans le navigateur).

## Ce que fait ce proto

- **Fond de carte** : CyclOSM (par défaut, orienté vélo — pistes cyclables,
  itinéraires cyclotouristiques) ou OpenTopoMap, via un toggle segmenté en
  haut à gauche.
- **Import GPX** : profil de dénivelé intégré directement sous la trace dans
  l'onglet "Trace" (mini-aperçu + agrandi avec curseur synchronisé sur la
  carte), distance et D+ calculés automatiquement.
- **Points d'intérêt** (regroupés en un seul onglet) : ateliers vélo
  (magasins, stations de réparation, gonflage, lavage, distributeurs de
  chambres à air — voir architecture ci-dessous), points d'eau (via l'API
  OpenDataSoft — dataset national pré-extrait d'OSM, mis à jour
  hebdomadairement), et hébergements + restaurants (DATAtourisme, un seul
  appel réseau alimente les deux couches — filtré côté client par type).
  Chaque restaurant affiche son type précis quand disponible (Restaurant,
  Bar à vin/bistrot, Cave/vignoble, Restaurant d'hôtel), sinon
  "Restauration" en générique.
- **Plusieurs jours** : mode "Hébergements" (recherche DATAtourisme le long
  de la trace, marge de 5 km, recalcul automatique des jours restants à
  l'édition manuelle) ou "Étapes libres" (segments égaux, ajustables en
  glissant un point sur la carte). Chaque jour peut être sélectionné
  ("Sélectionner ce jour") : affiche le D+ et le temps estimé de cette
  étape (heuristique vitesse + pénalité de dénivelé), met la portion de
  trace correspondante en surbrillance sur la carte (les autres jours
  s'estompent), et centre la vue dessus. En mode Hébergements, un jour
  sélectionné permet aussi d'assigner directement un hébergement en
  cliquant son marqueur sur la carte (bouton "Assigner" dans sa popup) —
  alternative au menu déroulant existant, pour choisir librement un
  hébergement même hors des suggestions les plus proches du point idéal.
- **Ateliers vélo autour de moi** : bouton dans la section Ateliers du
  panneau Points d'intérêt — cherche les 5 ateliers (magasins + stations de
  réparation confondus) les plus proches de la position GPS actuelle, avec
  un rayon de recherche croissant (3, 10, 30, 80, puis 150 km) qui s'arrête
  dès que 5 résultats sont trouvés. Réutilise le suivi GPS déjà actif si
  disponible, sinon demande une position ponctuelle. Liste triée par
  distance, marqueurs mis en évidence sur la carte.
- **Météo** : prévisions Météo-France (AROME/ARPEGE) via Open-Meteo, un
  point toutes les 2h de pédalage à 15 km/h, avec pause déjeuner d'1h
  insérée automatiquement — limite dure de 4 jours de prévision côté API.
- **Ma position** : suivi GPS en direct.
- **Export hors-ligne** : depuis l'onglet "Trace", une fois un GPX chargé,
  un curseur (1 à 5 km, soit 2 à 10 km de large) et un bouton "Télécharger
  pour hors-ligne" génèrent un fichier HTML unique et autonome — tuiles du
  fond de carte actif (CyclOSM ou OpenTopoMap), trace, et toutes les
  catégories de points (ateliers vélo, points d'eau, hébergements,
  restaurants) le long du corridor, embarqués en base64/JSON avec Leaflet
  lui-même. S'ouvre sans connexion réseau, avec sa propre légende
  filtrable et un bouton "Ma position" (le GPS fonctionne hors ligne).
- **Navigation** : menu flottant en bas (Trace / Points d'intérêt / Jours /
  Météo), chaque icône ouvre une tuile qui monte du bas.

## Architecture des POI vélo (magasins, réparation, gonflage, lavage, chambres à air)

Après plusieurs correctifs successifs pour limiter la charge envoyée à
Overpass (miroirs multiples, requêtes fusionnées, cache par zone) qui n'ont
fait que repousser le problème — jusqu'à un vrai `HTTP 429` observé sur un
déploiement réel début 2026, période de surcharge documentée sur le forum
officiel OpenStreetMap — le choix a été fait de sortir complètement
Overpass du chemin critique de l'app, plutôt que de continuer à rafistoler.

**Nouvelle architecture, en trois parties :**

1. **`scripts/update_poi.py`** — un script Python (bibliothèque standard
   uniquement, aucune dépendance à installer) qui interroge Overpass **une
   seule fois par jour** pour toute la France métropolitaine (zone
   `ISO3166-1=FR`), avec repli sur 4 miroirs et pause entre tentatives en
   cas d'échec. Écrit `data/cyclo_poi.geojson` (le fichier consolidé
   complet) puis le découpe en tuiles géographiques de 0,1° de côté
   (~11 km) dans `data/tiles/`, plus un `data/tiles/index.json` qui liste
   les tuiles réellement non vides.

2. **`.github/workflows/update-poi.yml`** — un modèle de workflow GitHub
   Actions (déclenchement manuel par défaut ; un cron quotidien est prêt
   en commentaire à activer) qui exécute le script et committe les
   fichiers `data/` s'ils ont changé.

3. **Côté client** — l'app ne télécharge plus jamais l'intégralité du jeu
   de données : elle charge une fois `data/tiles/index.json` (quelques Ko),
   calcule quelles tuiles couvrent la zone visible (+ une petite marge),
   et ne récupère que celles pas encore en mémoire. Une tuile chargée une
   fois n'est **jamais retéléchargée** — cache trivialement fiable, bien
   plus simple que l'ancien système de cache par zone+signature qui tentait
   de compenser un service tiers instable. Le filtrage par catégorie
   (magasins/réparation/gonflage/etc.) se fait en mémoire sur les points
   déjà chargés, sans aucun nouvel appel réseau. Cette même logique de
   tuiles alimente aussi "Ateliers autour de moi" et le corridor de
   l'export hors-ligne — plus aucune dépendance à Overpass en direct nulle
   part dans l'app.

**Point d'attention avant le premier déploiement** : `data/tiles/index.json`
est livré vide par défaut (`"tiles": []`) — l'app fonctionnera normalement
mais n'affichera aucun atelier vélo tant que `scripts/update_poi.py` n'aura
pas tourné au moins une fois (manuellement ou via le workflow). Les points
d'eau et hébergements/restaurants, sur d'autres sources, ne sont pas
concernés par cette limitation.

**Pourquoi une grille fixe de 0,1° plutôt qu'un système de zoom-tuiles
classique** : plus simple à générer et à consommer pour ce volume de
données (quelques dizaines de milliers de points en France), pas besoin de
la complexité d'un vrai système de tuiles multi-niveaux pensé pour des
rasters. À revoir si le volume de données croît significativement (plus de
catégories, DOM-TOM ajoutés, etc.).

## Historique des correctifs réseau (avant la migration vers les tuiles)

- 4 miroirs Overpass publics interrogés en parallèle puis séquentiellement,
  délais d'expiration explicites, repli par proxy CORS.
- **Points d'eau migrés vers OpenDataSoft (data.smartidf.services)** : un
  dataset national ("osm-france-drinking-water"), pré-extrait d'OSM et mis
  à jour hebdomadairement, via l'API v1 `geofilter.bbox`. **Niveau de
  confiance à nuancer** : l'existence de cette API et son paramètre sont
  documentés officiellement par OpenDataSoft, mais je n'ai pas pu tester un
  vrai appel depuis mon environnement (outils réseau restreints) — les noms
  exacts des champs de la réponse suivent le schéma standard OpenDataSoft
  mais n'ont pas été vérifiés sur ce dataset précis. Un log
  `[eau] réponse brute OpenDataSoft` reste dans la console pour diagnostic
  rapide si la couche n'affiche rien.
- Ces correctifs restent actifs pour la couche "Points d'eau" (toujours sur
  API distante) mais ne concernent plus les ateliers vélo, entièrement
  passés sur le système de tuiles ci-dessus.

## Relecture complète et correctifs (build 18)

Passe de relecture systématique après l'enchaînement de modifications
ciblées. Bugs trouvés et corrigés :

**Corrections fonctionnelles**
- *Marqueurs dupliqués* : une tuile POI pouvait être téléchargée deux fois
  si deux appels concurrents la demandaient (carte + "autour de moi"), ses
  points étant alors ajoutés en double. Registre des chargements en cours
  ajouté.
- *Assignation sur le dernier jour* : le dernier jour n'a pas de nuit (il
  finit à la fin de la trace) ; lui assigner un hébergement ajoutait une
  nuit et donc un jour entier au plan. Bouton masqué + garde dans la
  fonction.
- *Trace entièrement estompée* : une sélection de jour périmée (jour 3
  sélectionné puis recalcul à 2 jours) estompait tous les segments sans
  qu'aucun n'apparaisse sélectionné. Sélection réinitialisée au recalcul.
- *Marqueurs fantômes* : passer d'"Étapes libres" à "Hébergements"
  laissait les marqueurs draggables sur la carte. Nettoyage factorisé
  (`clearDayplanResults`).
- *Date météo vide* : produisait des horodatages `NaN` et une liste pleine
  de "Invalid Date" au lieu d'un message clair. Validation ajoutée.

**Performance**
- *Profil de dénivelé ~100× plus rapide* : l'ancienne version appelait
  `turf.along()` par point, chaque appel reparcourant toute la trace
  (quadratique — 205 ms pour 2 000 points, plusieurs secondes sur mobile
  pour une longue trace). Remplacé par un parcours linéaire à curseur.
  Vérifié équivalent au résultat précédent (écart max mesuré : 0,00 m).
- *Glissement des marqueurs fluide* : chaque événement de drag déclenchait
  un `nearestPointOnLine` + un redécoupage complet des segments (~60 ms sur
  5 000 points). Limité à une fois par frame via `requestAnimationFrame`,
  redécoupage reporté à la fin du glissement.
- *Longueur de trace mise en cache* : `turf.length()` était recalculé à 7
  endroits, jusque dans la boucle de glissement.
- *"Autour de moi" plafonné* : au palier 150 km, le bbox couvrait jusqu'à
  1 092 tuiles, soit autant de requêtes HTTP. Tri par proximité + plafond à
  60 tuiles, et concurrence limitée à 8 requêtes simultanées.
- *Météo* : les points au-delà de la fenêtre de 4 jours de l'API ne sont
  plus envoyés (ils étaient transmis pour rien).

**Robustesse et cohérence**
- *Alignement des tuiles Python/JS* : `floor(45.3 / 0.1)` vaut 452 et non
  453 (artefact de flottants). Les deux langages produisaient la même
  erreur, donc rien n'était cassé, mais la formule est désormais stabilisée
  des deux côtés par un epsilon partagé — vérifié par test croisé sur 205
  points (zéro divergence) et un test d'intégration bout-en-bout.
- *Détection du 429 côté script Python* : le test `status == 429` était du
  code mort (`urlopen` lève une exception avant). Le 429 est maintenant
  vraiment détecté, avec une pause plus longue. Supprimé aussi une attente
  de 15 s inutile après le dernier miroir.
- *Filtrage des propriétés Python* : `v not in (None, False)` aurait
  supprimé les valeurs `0` (`0 == False` en Python), et
  `has_compressed_air: false` était stocké sur chaque point.
- *Injection dans les popups* : l'uuid injecté dans un attribut `onclick`
  est validé par liste blanche de caractères.
- *Code mort supprimé* : `poiTileId()` jamais appelée, `popupForOsmNode()`
  qui ne servait plus que pour l'eau tout en affichant des champs
  d'ateliers vélo que la source OpenDataSoft ne fournit pas (remplacée par
  `popupForWaterPoint()`).

## Point d'attention pour l'exploitation

`data/cyclo_poi.geojson` (fichier consolidé complet) est committé à chaque
exécution quotidienne. Sur plusieurs mois, l'historique Git grossira
notablement — le fichier est utile comme export brut, mais si le poids
devient un souci, ne committer que `data/tiles/` suffit au fonctionnement
de l'app.


## Point bloquant à résoudre avant que "Hébergements" fonctionne

**DATAtourisme nécessite une clé API**, contrairement aux autres sources de
ce projet — gratuite mais sur inscription manuelle (nom, prénom, email) via
<https://www.datatourisme.fr/utiliser-les-donnees>. Impossible de la générer
par avance : il faut que l'utilisateur la demande lui-même, puis la colle
dans le champ dédié du panneau "Dormir" (elle est ensuite gardée en
mémoire locale, pas besoin de la recoller à chaque visite).

## Ce qui n'a pas pu être vérifié en conditions réelles

- **Script `update_poi.py` — logique testée, pas l'appel réseau réel** :
  les fonctions de transformation (catégorisation, extraction GeoJSON,
  découpage en tuiles) ont été testées avec des données Overpass factices
  et fonctionnent comme attendu. La requête Overpass réelle sur toute la
  France (potentiellement longue, plusieurs minutes) n'a en revanche jamais
  été exécutée pour de vrai — impossible depuis mon environnement
  (domaine non autorisé). À lancer manuellement une première fois pour
  vérifier le temps d'exécution réel et le volume de données obtenu avant
  d'activer le cron.
- **Chargement des tuiles côté client** : la logique (calcul des tuiles
  nécessaires, cache "jamais retéléchargé", filtrage en mémoire) a été
  vérifiée syntaxiquement mais jamais testée avec de vraies tuiles
  générées, faute d'avoir pu exécuter le script Python en conditions
  réelles dans cette session.

- **Structure DATAtourisme — résolu** : confirmé sur un vrai échantillon de
  20 lieux (bbox Grenoble/Vercors). `isLocatedAt` et `address` sont des
  tableaux (`isLocatedAt[0].geo`, `isLocatedAt[0].address[0]`), le libellé
  est étiqueté par langue au format JSON-LD (`label['@fr']`), et le filtre
  hébergement repose sur `type.includes('Accommodation')` ou
  `type.includes('LodgingBusiness')` — confirmé sur un vrai hôtel
  (Hôtel Lesdiguières, Grenoble) présent dans l'échantillon.
- **Densité d'hébergements/restaurants dans les résultats — à surveiller** :
  sur l'échantillon de 20 lieux testé, seuls 1 hébergement et ~7 restaurants
  étaient présents. Avec une page limitée à 100 résultats bruts avant
  filtrage, une zone large pourrait renvoyer peu de points pour chaque
  couche malgré une limite qui semble généreuse. Pas de pagination
  multi-pages implémentée — à ajouter si les résultats s'avèrent trop
  clairsemés en usage réel.
- **Type de restaurant** : extrait du tableau `type` quand une valeur plus
  précise que "FoodEstablishment" est présente (Restaurant, BistroOrWineBar,
  Winery...), mais l'échantillon testé montre que beaucoup de restaurants
  n'ont *que* le tag générique, sans sous-type — dans ce cas, l'app affiche
  juste "Restauration", pas une vraie catégorie de cuisine.
- **Export hors-ligne — pas encore testé en conditions réelles** : la
  génération elle-même a été vérifiée de façon automatisée (syntaxe
  validée, y compris avec des cas limites comme des guillemets ou
  `</script>` dans un nom de point), mais jamais ouverte dans un vrai
  navigateur ni testée hors ligne sur le terrain. Le fichier n'inclut pas
  les hébergements/restaurants si aucune clé DATAtourisme n'est
  renseignée — comportement voulu, pas un bug.
- **Temps estimé par jour** : calculé via une heuristique simple (vitesse
  forfaitaire + ~4 min ajoutées par 100 m de D+), pas un modèle physique
  précis — à ajuster si les temps affichés semblent trop optimistes ou
  pessimistes à l'usage réel.
- **Assignation manuelle d'hébergement** : jamais testée en conditions
  réelles. Le bouton "Assigner" n'apparaît dans une popup qu'après
  régénération de son contenu à l'ouverture — comportement voulu (pour
  refléter le jour sélectionné au moment du clic), mais à confirmer que ça
  fonctionne bien visuellement.
- **Icônes de navigation** : SVG faits à la main, pas testés visuellement.
