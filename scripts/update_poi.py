#!/usr/bin/env python3
"""
Met à jour les POI vélo (magasins, réparation, gonflage, lavage,
distributeurs de chambres à air) pour toute la France depuis Overpass.

Écrit :
  - data/cyclo_poi.geojson       — le fichier consolidé complet
  - data/tiles/<lat>_<lon>.geojson — un fichier par cellule de grille (0.1°)
  - data/tiles/index.json        — liste des tuiles non vides + métadonnées

Conçu pour tourner sans surveillance (cron GitHub Actions, 1x/jour) : pas de
dépendance externe (bibliothèque standard uniquement), plusieurs miroirs
Overpass essayés avec repli en cas d'échec, requête unique par exécution
(pas de boucle par zone qui multiplierait les appels).

Portée actuelle : France métropolitaine (zone OSM ISO3166-1=FR,
admin_level=2). Les DOM-TOM ne sont pas couverts par cette zone — à
étendre séparément si besoin un jour.
"""

import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# =============================================================
# Configuration
# =============================================================

# Mêmes miroirs que ceux identifiés et éprouvés côté client — un script
# planifié peut se permettre d'être patient (délais longs, pauses entre
# tentatives) puisqu'il ne bloque aucun utilisateur en direct.
OVERPASS_MIRRORS = [
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

# Délai serveur (dans la requête Overpass elle-même) et délai client HTTP —
# volontairement généreux : une requête sur toute la France est lourde, et
# c'est un job de fond, pas une réponse attendue par un utilisateur.
OVERPASS_SERVER_TIMEOUT_S = 600
HTTP_TIMEOUT_S = 300

# Pause avant de basculer sur le miroir suivant en cas d'échec — laisse le
# temps à un éventuel pic de charge de retomber, et évite de marteler
# plusieurs serveurs coup sur coup. Pause plus longue après un 429, où le
# serveur nous demande explicitement de lever le pied.
RETRY_DELAY_S = 15
RATE_LIMIT_DELAY_S = 60

# Taille de la grille de tuiles, en degrés (~11 km de côté en latitude).
TILE_SIZE_DEG = 0.1
# Marge anti-artefact de flottants pour le calcul d'indice de tuile —
# doit rester identique côté client (voir POI_TILE_EPS dans index.html).
TILE_EPS = 1e-9

# Catégories ciblées, avec le libellé utilisé côté client (l'app vélo).
# "compressed_air=yes" en tag additionnel sur un shop/repair n'est PAS une
# catégorie séparée ici — c'est lu comme un attribut du point existant.
OVERPASS_QUERY = f"""
[out:json][timeout:{OVERPASS_SERVER_TIMEOUT_S}];
area["ISO3166-1"="FR"][admin_level=2]->.fr;
(
  node["shop"="bicycle"](area.fr);
  node["amenity"="bicycle_repair_station"](area.fr);
  node["amenity"="compressed_air"](area.fr);
  node["amenity"="bicycle_wash"](area.fr);
  node["amenity"="vending_machine"]["vending"="bicycle_tube"](area.fr);
);
out body;
""".strip()

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
TILES_DIR = DATA_DIR / "tiles"
GEOJSON_PATH = DATA_DIR / "cyclo_poi.geojson"
INDEX_PATH = TILES_DIR / "index.json"


def log(msg):
    print(f"[update_poi] {msg}", flush=True)


# =============================================================
# Appel Overpass avec repli sur plusieurs miroirs
# =============================================================

def fetch_overpass_data(query):
    """Tente chaque miroir dans l'ordre, avec une pause entre les essais.
    Lève une exception si tous échouent — un job planifié doit échouer
    bruyamment plutôt que d'écrire un fichier vide silencieusement."""
    last_error = None
    total = len(OVERPASS_MIRRORS)

    for attempt, mirror in enumerate(OVERPASS_MIRRORS, start=1):
        log(f"Tentative {attempt}/{total} sur {mirror}")
        rate_limited = False
        try:
            data = urllib.parse.urlencode({"data": query}).encode("utf-8")
            req = urllib.request.Request(
                mirror,
                data=data,
                method="POST",
                # Overpass recommande un User-Agent descriptif identifiant
                # l'application — bonne pratique côté "bon citoyen" d'une API
                # publique partagée, indépendante du souci de proxy
                # d'entreprise rencontré en test local.
                headers={"User-Agent": "CycloTrip-POI-Updater/1.0 (script de mise a jour quotidienne, usage personnel)"},
            )
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as response:
                body = response.read()
                result = json.loads(body)
                elements = result.get("elements", [])
                log(f"Succès sur {mirror} — {len(elements)} élément(s) reçu(s)")
                return result
        except urllib.error.HTTPError as e:
            # urlopen lève HTTPError pour tout code >= 400 : c'est ICI qu'on
            # voit un 429, pas dans une inspection de response.status (qui
            # ne serait jamais atteinte pour un code d'erreur).
            last_error = e
            rate_limited = (e.code == 429)
            label = "limitation de débit" if rate_limited else "erreur HTTP"
            log(f"Échec sur {mirror} — {label} {e.code} {e.reason}")
        except Exception as e:  # noqa: BLE001 — capture large volontaire : on passe au miroir suivant
            last_error = e
            log(f"Échec sur {mirror} — {e}")

        # Pas de pause après le dernier miroir : elle retarderait l'échec
        # final de plusieurs dizaines de secondes pour rien.
        if attempt < total:
            delay = RATE_LIMIT_DELAY_S if rate_limited else RETRY_DELAY_S
            log(f"Pause de {delay}s avant le miroir suivant…")
            time.sleep(delay)

    raise RuntimeError(f"Tous les miroirs Overpass ont échoué. Dernière erreur : {last_error}")


# =============================================================
# Transformation en GeoJSON
# =============================================================

def categorize(tags):
    """Détermine la catégorie applicative à partir des tags OSM bruts."""
    if tags.get("shop") == "bicycle":
        return "shop"
    if tags.get("amenity") == "bicycle_repair_station":
        return "repair"
    if tags.get("amenity") == "compressed_air":
        return "compressed_air"
    if tags.get("amenity") == "bicycle_wash":
        return "wash"
    if tags.get("amenity") == "vending_machine" and tags.get("vending") == "bicycle_tube":
        return "tube_vending"
    return None  # ne devrait pas arriver vu la requête, filtré par sécurité


def build_geojson(overpass_result):
    features = []
    skipped = 0

    for el in overpass_result.get("elements", []):
        if el.get("type") != "node":
            skipped += 1
            continue
        lat, lon = el.get("lat"), el.get("lon")
        if lat is None or lon is None:
            skipped += 1
            continue

        tags = el.get("tags", {}) or {}
        category = categorize(tags)
        if category is None:
            skipped += 1
            continue

        properties = {
            "category": category,
            "name": tags.get("name"),
            "opening_hours": tags.get("opening_hours"),
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "website": tags.get("website"),
            "osm_id": el.get("id"),
        }
        # Compacte le fichier : on retire les clés sans valeur. Test
        # explicite sur None — un `v not in (None, False)` supprimerait
        # aussi les valeurs 0, puisque 0 == False en Python.
        properties = {k: v for k, v in properties.items() if v is not None}

        # Drapeau ajouté seulement quand il est vrai : le stocker à False
        # sur chaque point gonflerait le fichier sans rien apprendre
        # (l'absence de clé se lit comme "pas de gonflage signalé").
        if tags.get("compressed_air") == "yes":
            properties["has_compressed_air"] = True

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": properties,
        })

    if skipped:
        log(f"{skipped} élément(s) ignoré(s) (type non-node, coordonnées manquantes, ou catégorie non reconnue)")

    return {"type": "FeatureCollection", "features": features}


# =============================================================
# Découpage en tuiles
# =============================================================

def tile_id_for(lat, lon):
    # L'epsilon absorbe les artefacts de flottants : sans lui,
    # math.floor(45.3 / 0.1) vaut 452 au lieu de 453 (45.3/0.1 = 452.999…).
    # ATTENTION : la formule doit rester rigoureusement identique à
    # poiTileIdsForBounds() dans index.html, sinon le client cherchera des
    # tuiles que ce script n'a jamais écrites.
    lat_idx = math.floor(lat / TILE_SIZE_DEG + TILE_EPS)
    lon_idx = math.floor(lon / TILE_SIZE_DEG + TILE_EPS)
    return f"{lat_idx}_{lon_idx}"


def write_tiles(geojson):
    TILES_DIR.mkdir(parents=True, exist_ok=True)

    # Repart d'un dossier de tuiles propre à chaque exécution — évite de
    # laisser traîner une tuile devenue vide (point supprimé d'OSM depuis
    # la dernière mise à jour) qui resterait servie indéfiniment sinon.
    for old_file in TILES_DIR.glob("*.geojson"):
        old_file.unlink()

    buckets = {}
    for feature in geojson["features"]:
        lon, lat = feature["geometry"]["coordinates"]
        tid = tile_id_for(lat, lon)
        buckets.setdefault(tid, []).append(feature)

    for tid, features in buckets.items():
        tile_path = TILES_DIR / f"{tid}.geojson"
        tile_geojson = {"type": "FeatureCollection", "features": features}
        tile_path.write_text(json.dumps(tile_geojson, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    index = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tile_size_deg": TILE_SIZE_DEG,
        "tile_count": len(buckets),
        "point_count": len(geojson["features"]),
        "tiles": sorted(buckets.keys()),
    }
    INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    log(f"{len(buckets)} tuile(s) écrite(s), {len(geojson['features'])} point(s) au total")


# =============================================================
# Point d'entrée
# =============================================================

def main():
    log("Démarrage de la mise à jour des POI vélo")
    log(f"Requête Overpass : {len(OVERPASS_QUERY)} caractères, zone France métropolitaine")

    overpass_result = fetch_overpass_data(OVERPASS_QUERY)
    geojson = build_geojson(overpass_result)

    if len(geojson["features"]) == 0:
        # Garde-fou : mieux vaut échouer et garder les anciennes données
        # que d'écraser un jeu de données valide par un fichier vide (ex.
        # en cas de réponse Overpass malformée passée entre les mailles).
        log("ERREUR : 0 point extrait, abandon sans écrire (données existantes conservées).")
        sys.exit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    GEOJSON_PATH.write_text(json.dumps(geojson, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"Fichier consolidé écrit : {GEOJSON_PATH} ({len(geojson['features'])} points)")

    write_tiles(geojson)
    log("Terminé avec succès.")


if __name__ == "__main__":
    main()
