from app.services.lot_import import _map_finish


def test_map_finish_for_new_lot_details() -> None:
    assert _map_finish("Poke Ball Reverse Holo", "Normal") == "Poke Ball Reverse Holo"
    assert _map_finish("pokeball reverse holo", "Normal") == "Poke Ball Reverse Holo"
    assert _map_finish("Reverse Holo (Reverse Foil)", "Normal") == "Reverse Holo (Reverse Foil)"
    assert (
        _map_finish("Reverse Holo Element (Reverse Foil)", "Normal")
        == "Reverse Holo Element (Reverse Foil)"
    )


def test_map_finish_handles_ocr_variants() -> None:
    assert (
        _map_finish("Rêvérse Hóló élément reverse foil", "Normal")
        == "Reverse Holo Element (Reverse Foil)"
    )
    assert (
        _map_finish("reverse holo elemnt reverse foil", "Normal")
        == "Reverse Holo Element (Reverse Foil)"
    )
    assert _map_finish("masterball reverse holo", "Normal") == "Master Ball Reverse Holo"


def test_map_finish_falls_back_to_default() -> None:
    assert _map_finish(None, "Normal") == "Normal"
    assert _map_finish("", "Normal") == "Normal"
