"""Post-traitements des villes candidates avant scoring."""

from rich.console import Console
from agents.common.geo import haversine_km, normalize_place_name, resolve_reference_city

console = Console()


def apply_reference_city_filter(city_dicts: list[dict], user_profile: dict) -> list[dict]:
    """
    Filtre précisément les villes à partir d'une ville de référence.
    Le SQL applique seulement une bounding box ; ici on applique la vraie distance Haversine.

    Ajoute distance_reference_km dans les résultats, sans stocker ce champ en base.
    """
    ville_ref = normalize_place_name(user_profile.get("ville_reference", ""))
    rayon_ref = user_profile.get("rayon_km") or user_profile.get("rayon_reference_km")

    if not ville_ref or not rayon_ref:
        return city_dicts

    coords = resolve_reference_city(ville_ref)
    if not coords:
        console.print(f"[yellow]⚠️  Ville de référence inconnue : {ville_ref}. Filtre ignoré.[/yellow]")
        return city_dicts

    try:
        rayon = float(rayon_ref)
    except Exception:
        rayon = 80.0

    ref_lat, ref_lon = coords
    exclude_ref = bool(user_profile.get("exclure_ville_reference", False))
    filtered = []

    for city in city_dicts:
        lat = city.get("latitude")
        lon = city.get("longitude")
        if lat is None or lon is None:
            continue

        city_name_norm = normalize_place_name(city.get("nom", ""))
        if exclude_ref and city_name_norm == ville_ref:
            continue

        dist = haversine_km(float(ref_lat), float(ref_lon), float(lat), float(lon))
        if dist <= rayon:
            city["distance_reference_km"] = round(dist, 1)
            filtered.append(city)

    filtered.sort(key=lambda c: c.get("distance_reference_km", 999999))
    console.print(f"[dim]Filtre exact proximité {ville_ref} ≤ {rayon:.0f} km : {len(filtered)} villes[/dim]")
    return filtered



def apply_budget_surface_estimate(city_dicts: list[dict], user_profile: dict) -> list[dict]:
    """
    Ajoute surface_estimable_m2 aux résultats si l'utilisateur a donné un budget.
    Ne stocke rien en base.
    """
    budget = user_profile.get("budget_immobilier")
    if not budget:
        return city_dicts

    try:
        budget_float = float(budget)
    except Exception:
        return city_dicts

    for city in city_dicts:
        prix = city.get("prix_immo_m2")
        if prix:
            try:
                city["surface_estimable_m2"] = round(budget_float / float(prix), 1)
            except Exception:
                city["surface_estimable_m2"] = None
        else:
            city["surface_estimable_m2"] = None

    return city_dicts




def add_population_category(city_dicts: list[dict]) -> list[dict]:
    """Ajoute une catégorie lisible de taille de ville aux résultats."""
    for city in city_dicts:
        pop = city.get("population") or 0
        try:
            pop = int(pop)
        except Exception:
            pop = 0

        if pop < 10000:
            category = "village / très petite ville"
        elif pop < 50000:
            category = "petite ville"
        elif pop < 150000:
            category = "ville moyenne"
        else:
            category = "grande ville"

        city["taille_ville"] = category

    return city_dicts

