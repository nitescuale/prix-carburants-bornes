from carburants import irve


def test_url_fichier_consolide_prend_le_plus_recent(monkeypatch):
    class Reponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "resources": [
                    {"format": "csv", "title": "Fichier consolidé ancien", "last_modified": "2024-01-01", "url": "a"},
                    {"format": "csv", "title": "Fichier consolidé", "last_modified": "2026-10-01", "url": "b"},
                    {"format": "geojson", "title": "Fichier consolidé", "last_modified": "2026-10-02", "url": "c"},
                ]
            }

    monkeypatch.setattr(irve.requests, "get", lambda url, timeout: Reponse())
    assert irve.url_fichier_consolide() == "b"


def test_lire_csv_garde_le_texte():
    contenu = "id_pdc_itinerance,puissance_nominale,code_insee_commune\nFRS01E001,22,01004\n".encode()
    bornes = irve.lire_csv(contenu)
    assert bornes.row(0, named=True) == {
        "id_pdc_itinerance": "FRS01E001",
        "puissance_nominale": "22",
        "code_insee_commune": "01004",
    }
