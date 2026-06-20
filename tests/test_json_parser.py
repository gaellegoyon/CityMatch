from agents.profile.json_parser import extract_criteria_from_response


def test_extract_direct_json() -> None:
    response = '{"criteres": {"prix_immo_m2": 5}, "budget_immobilier": 200000}'

    assert extract_criteria_from_response(response) == {
        "criteres": {"prix_immo_m2": 5},
        "budget_immobilier": 200000,
    }


def test_extract_json_markdown_block() -> None:
    response = """
    Voici le profil :
    ```json
    {
      "criteres": {
        "distance_mer_km": 5,
        "score_securite": 4
      },
      "population_min": 10000
    }
    ```
    """

    result = extract_criteria_from_response(response)

    assert result is not None
    assert result["criteres"]["distance_mer_km"] == 5
    assert result["criteres"]["score_securite"] == 4
    assert result["population_min"] == 10000


def test_extract_generic_markdown_block() -> None:
    response = """
    ```
    {"criteres": {"fibre_pct": 5}}
    ```
    """

    assert extract_criteria_from_response(response) == {
        "criteres": {"fibre_pct": 5}
    }


def test_extract_embedded_balanced_json() -> None:
    response = """
    Analyse terminée.
    Résultat: {"criteres": {"prix_immo_m2": 4, "taux_chomage": 3}, "regions_preferees": ["Bretagne"]}
    Fin.
    """

    result = extract_criteria_from_response(response)

    assert result is not None
    assert result["criteres"]["prix_immo_m2"] == 4
    assert result["regions_preferees"] == ["Bretagne"]


def test_returns_none_without_json() -> None:
    assert extract_criteria_from_response("Je n'ai pas assez d'information.") is None


def test_returns_none_when_json_has_no_criteres() -> None:
    assert extract_criteria_from_response('{"foo": "bar"}') is None


def test_returns_none_when_criteres_is_not_dict() -> None:
    assert extract_criteria_from_response('{"criteres": ["prix_immo_m2"]}') is None


def test_handles_invalid_json_block_then_valid_json() -> None:
    response = """
    ```json
    {"criteres": }
    ```

    Puis correction :
    ```json
    {"criteres": {"score_securite": 5}}
    ```
    """

    assert extract_criteria_from_response(response) == {
        "criteres": {"score_securite": 5}
    }