from carburants import irve


def test_url_fichier_consolide_prend_le_plus_recent(monkeypatch):
    class Reponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "resources": [
                    {
                        "type": "main",
                        "format": "csv",
                        "title": "Consolidation de la dernière version à date du schéma (v2.3.1) - 20261019",
                        "last_modified": "2026-10-19T03:21:31",
                        "url": "derniere",
                    },
                    {
                        "type": "main",
                        "format": "csv",
                        "title": "Consolidation de la v2.3.1 du schéma - 20261019",
                        "last_modified": "2026-10-19T03:21:16",
                        "url": "v231",
                    },
                    {"type": "main", "format": "geojson", "title": "Export geojson", "last_modified": "2026-10-19T03:22:22", "url": "geojson"},
                    {
                        "type": "documentation",
                        "format": "csv",
                        "title": "Documentation sur la consolidation - 20261019",
                        "last_modified": "2026-10-19T03:23:02",
                        "url": "rapport",
                    },
                ]
            }

    monkeypatch.setattr(irve.requests, "get", lambda url, timeout: Reponse())
    assert irve.url_fichier_consolide() == "derniere"


def test_lire_csv_garde_le_texte():
    contenu = "id_pdc_itinerance,puissance_nominale,code_insee_commune\nFRS01E001,22,01004\n".encode()
    bornes = irve.lire_csv(contenu)
    assert bornes.row(0, named=True) == {
        "id_pdc_itinerance": "FRS01E001",
        "puissance_nominale": "22",
        "code_insee_commune": "01004",
    }
