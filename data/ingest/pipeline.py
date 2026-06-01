"""
data/ingest/pipeline.py

Assemblage des sources et construction d'un dictionnaire City par commune.
"""

from __future__ import annotations

import pandas as pd

from data.ingest.config import KEPT_CITY_FIELDS
from data.ingest.geo import POINTS_LITTORAL, ZONES_MONTAGNE, distance_to_nearest
from data.ingest.sources.arcep import extract_fibre_pct
from data.ingest.sources.atmo import extract_qualite_air_score
from data.ingest.sources.bpe import extract_bpe_for_commune
from data.ingest.sources.climate import get_climat
from data.ingest.sources.crime import extract_criminalite
from data.ingest.sources.dvf import compute_prix_immo
from data.ingest.sources.insee import extract_demo_indicators, extract_rp_indicators


def apply_postprocess_fallbacks(results: list[tuple]) -> list[tuple]:
    """
    Fallbacks post-traitement limités aux champs fiables mais parfois incomplets.

    Principes :
    - prix_immo_m2 : fallback médiane département, puis région, puis nationale ;
    - score_securite : fallback médiane région, puis nationale ;
    - pas de fallback air : qualite_air_score reste NULL si aucune vraie source ATMO ;
    - pas de fallback fibre : fibre_pct reste une vraie donnée ARCEP ;
    - conversion numérique explicite avant les médianes pandas.

    Cette fonction corrige le bug pandas :
    TypeError: Cannot use numeric_only=True with SeriesGroupBy.median and non-numeric dtypes.
    """
    ok_items = [
        (code, nom, data, err)
        for code, nom, data, err in results
        if err is None and data
    ]
    if not ok_items:
        return results

    df = pd.DataFrame([data for _, _, data, _ in ok_items])

    def fill_by_group(field: str, groups: list[str]) -> None:
        if field not in df.columns:
            return

        # Conversion explicite : évite le bug pandas SeriesGroupBy.median
        # avec colonnes object/string.
        df[field] = pd.to_numeric(df[field], errors="coerce")

        for group in groups:
            if group not in df.columns:
                continue

            med = df.groupby(group, dropna=True)[field].median()

            for _, _, data, _ in ok_items:
                if data.get(field) is not None:
                    continue

                key = data.get(group)
                if key is None or key not in med.index:
                    continue

                value = med.get(key)
                if pd.notna(value):
                    data[field] = round(float(value), 1)

        national_values = df[field].dropna()
        if national_values.empty:
            return

        national_median = float(national_values.median())
        for _, _, data, _ in ok_items:
            if data.get(field) is None:
                data[field] = round(national_median, 1)

    fill_by_group("prix_immo_m2", ["departement", "region"])
    fill_by_group("score_securite", ["region"])

    # Ne pas estimer :
    # - qualite_air_score
    # - fibre_pct

    for index, (code, nom, data, err) in enumerate(results):
        if err is None and data:
            results[index] = (
                code,
                nom,
                {key: value for key, value in data.items() if key in KEPT_CITY_FIELDS},
                err,
            )

    return results


def build_commune_data(code_insee, nom, dept, region, lat, lon,
                       bpe_df, crime_df, arcep_df, communes_df,
                       atmo_df=None, no_api: bool = False) -> dict:
    """
    Construit uniquement les champs conservés dans le modèle simplifié.

    Les sources expérimentales ou trop estimées sont volontairement exclues :
    GBIF, pollution sols, risque sismique. ARCEP et ATMO uniquement si vraie donnée communale.
    """
    dep = str(code_insee)[:2]

    city = {
        "code_insee": code_insee,
        "nom": nom,
        "departement": dept,
        "region": region,
        "latitude": lat,
        "longitude": lon,
    }

    rp = extract_rp_indicators(communes_df, code_insee)
    pop = rp.get("population") or 50000
    pop_safe = max(pop, 1)

    city.update({
        "population": pop,
        "taux_chomage": rp.get("taux_chomage"),
        "age_median": rp.get("age_median"),
        "revenu_median": rp.get("revenu_median"),
        "taux_logements_vacants": rp.get("taux_logements_vacants"),
        "pct_moins_15_ans": rp.get("pct_moins_15_ans"),
        "pct_plus_65_ans": rp.get("pct_plus_65_ans"),
        "nb_entreprises": rp.get("nb_entreprises"),
        "entreprises_pour_1000": rp.get("entreprises_pour_1000"),
    })

    bpe = extract_bpe_for_commune(bpe_df, code_insee)

    # On garde les équipements BPE clairement identifiés et réellement utiles.
    for field in [
        "nb_creches", "nb_ecoles_primaires", "nb_colleges", "nb_lycees",
        "nb_medecins_generalistes", "nb_pharmacies", "nb_hopitaux", "nb_gares",
        "nb_piscines", "nb_bibliotheques", "nb_supermarches", "nb_restaurants",
        "nb_equipements_sportifs", "nb_cinemas", "nb_dentistes",
        "nb_ophtalmologues", "nb_pediatres", "nb_urgences",
    ]:
        city[field] = bpe.get(field, 0)

    # Ratios normalisés : plus exploitables que les volumes bruts seuls.
    city["creches_pour_1000"] = round(city["nb_creches"] / (pop_safe / 1000), 3)
    city["medecins_pour_1000"] = round(city["nb_medecins_generalistes"] / (pop_safe / 1000), 3)
    city["supermarches_pour_1000"] = round(city["nb_supermarches"] / (pop_safe / 1000), 3)
    city["nb_pharmacies_pour_1000"] = round(city["nb_pharmacies"] / (pop_safe / 1000), 3)

    nb_spe = city["nb_dentistes"] + city["nb_ophtalmologues"] + city["nb_pediatres"]
    city["medecins_specialistes_pour_1000"] = round(nb_spe / (pop_safe / 1000), 3)

    pct_enfants = city.get("pct_moins_15_ans") / 100 if city.get("pct_moins_15_ans") else 0.15
    nb_enfants = max(int(pop * pct_enfants), 1)
    city["ecoles_pour_1000_enfants"] = round(city["nb_ecoles_primaires"] / (nb_enfants / 1000), 3)
    city["nb_lycees_pour_1000_ados"] = round(city["nb_lycees"] / max(int(pop * 0.05), 1) * 1000, 3)

    city["score_restauration"] = round(min(10, city["nb_restaurants"] / (pop_safe / 1000) * 2), 1)
    city["transport_score"] = float(min(10, city["nb_gares"] * 3 + min(7, pop // 40000)))

    crime = extract_criminalite(crime_df, code_insee, pop)
    city.update(crime)
    if crime.get("criminalite_pour_1000") is not None:
        city["score_securite"] = round(max(0, 10 - crime["criminalite_pour_1000"] / 15), 1)
    else:
        city["score_securite"] = None

    city["prix_immo_m2"] = compute_prix_immo(code_insee, dep)
    city.update(extract_demo_indicators(communes_df, code_insee, pop))

    city["fibre_pct"] = extract_fibre_pct(arcep_df, code_insee)
    city["qualite_air_score"] = extract_qualite_air_score(atmo_df if atmo_df is not None else pd.DataFrame(), code_insee)
    city.update(get_climat(region))
# Géographie objective calculée depuis coordonnées.
    city["distance_mer_km"] = round(distance_to_nearest(lat, lon, POINTS_LITTORAL), 1)
    city["distance_montagne_km"] = round(distance_to_nearest(lat, lon, ZONES_MONTAGNE), 1)

    return {k: v for k, v in city.items() if k in KEPT_CITY_FIELDS}
