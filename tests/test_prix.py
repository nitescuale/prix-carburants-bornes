import io
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from carburants import prix

FIXTURE = Path(__file__).parent / "fixtures" / "pdv.xml"


@pytest.fixture
def tables():
    return prix.parser_xml(FIXTURE.read_bytes())


def test_stations(tables):
    stations, _ = tables
    assert stations["id"].to_list() == [1000001, 13100005]
    premiere = stations.row(0, named=True)
    assert premiere["latitude"] == pytest.approx(46.20114)
    assert premiere["longitude"] == pytest.approx(5.19791)
    assert premiere["ville"] == "SAINT-DENIS-LÈS-BOURG"
    assert premiere["type_route"] == "route"
    assert premiere["automate_24_24"] is True
    assert premiere["services"] == ["Station de gonflage", "Boutique alimentaire"]
    assert stations.row(1, named=True)["type_route"] == "autoroute"


def test_prix_euros_et_milliemes(tables):
    _, tarifs = tables
    gazole = tarifs.filter(station_id=1000001, carburant="Gazole").row(0, named=True)
    assert gazole["prix"] == pytest.approx(1.739)
    assert gazole["maj"] == datetime(2025, 1, 2, 9, 25, 22)
    ancien = tarifs.filter(station_id=13100005).row(0, named=True)
    assert ancien["prix"] == pytest.approx(1.259)


def test_doublons_supprimes_et_ruptures_ignorees(tables):
    _, tarifs = tables
    assert len(tarifs) == 3
    assert "SP95" not in tarifs["carburant"].to_list()


def test_format_iso_t(tables):
    _, tarifs = tables
    sp98 = tarifs.filter(carburant="SP98").row(0, named=True)
    assert sp98["maj"] == datetime(2025, 1, 2, 9, 25, 22)


def test_telecharger_xml_extrait_le_zip(monkeypatch):
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        archive.writestr("PrixCarburants_instantane.xml", FIXTURE.read_bytes())

    class Reponse:
        content = tampon.getvalue()

        def raise_for_status(self):
            pass

    monkeypatch.setattr(prix.requests, "get", lambda url, timeout: Reponse())
    assert prix.telecharger_xml(prix.URL_INSTANTANE) == FIXTURE.read_bytes()


def test_parser_accepte_un_flux():
    with FIXTURE.open("rb") as flux:
        stations, tarifs = prix.parser_xml(flux)
    assert stations.height == 2
    assert tarifs.height == 3


def test_collecter_zip_lit_l_archive_sur_disque(monkeypatch):
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        archive.writestr("PrixCarburants_annuel_2008.xml", FIXTURE.read_bytes())
    contenu = tampon.getvalue()

    class Reponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            for debut in range(0, len(contenu), 100):
                yield contenu[debut : debut + 100]

    monkeypatch.setattr(prix.requests, "get", lambda url, timeout, stream: Reponse())
    stations, tarifs = prix.collecter_zip(prix.url_annee(2008))
    assert stations["id"].to_list() == [1000001, 13100005]
    assert tarifs.height == 3
