from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from unittest import TestCase

from rasterio.crs import CRS
from rasterio.warp import transform

from sentinel2_mt.area import (
    Municipio,
    bbox_de_geojson,
    buscar_municipios,
    carregar_municipios,
    importar_area,
    normalizar_texto,
)


RAIZ = Path(__file__).resolve().parents[1]


def _poligono(oeste: float, sul: float, leste: float, norte: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [[oeste, sul], [leste, sul], [leste, norte], [oeste, norte], [oeste, sul]]
        ],
    }


def _escrever_shp(caminho: Path, bbox: tuple[float, float, float, float], tipo: int = 5) -> None:
    cabecalho = bytearray(100)
    cabecalho[0:4] = struct.pack(">i", 9994)
    cabecalho[24:28] = struct.pack(">i", 50)
    cabecalho[28:32] = struct.pack("<i", 1000)
    cabecalho[32:36] = struct.pack("<i", tipo)
    cabecalho[36:68] = struct.pack("<4d", *bbox)
    caminho.write_bytes(bytes(cabecalho))


class TestImportacaoArea(TestCase):
    def setUp(self) -> None:
        self.temporario = TemporaryDirectory()
        self.pasta = Path(self.temporario.name)

    def tearDown(self) -> None:
        self.temporario.cleanup()

    def _json(self, nome: str, dados: dict) -> Path:
        caminho = self.pasta / nome
        caminho.write_text(json.dumps(dados), encoding="utf-8")
        return caminho

    def test_feature_collection_usa_retangulo_envolvente_e_nome(self) -> None:
        caminho = self._json(
            "talhao.geojson",
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"nome": "Fazenda Boa Vista"},
                        "geometry": {
                            "type": "MultiPolygon",
                            "coordinates": [
                                _poligono(-55.9, -12.7, -55.6, -12.4)["coordinates"],
                                _poligono(-55.5, -12.9, -55.4, -12.8)["coordinates"],
                            ],
                        },
                    }
                ],
            },
        )

        area = importar_area(caminho)

        self.assertEqual(area.nome, "Fazenda Boa Vista")
        self.assertEqual(area.bbox, (-55.9, -12.9, -55.4, -12.4))
        self.assertEqual(area.crs_origem, "OGC:CRS84")
        self.assertIn("retângulo envolvente", " ".join(area.avisos))

    def test_geometria_simples_sem_nome_usa_nome_do_arquivo(self) -> None:
        area = importar_area(self._json("minha_area.json", _poligono(-56, -13, -55, -12)))

        self.assertEqual(area.nome, "minha_area")
        self.assertEqual(area.bbox, (-56.0, -13.0, -55.0, -12.0))

    def test_crs_legado_projetado_e_reprojetado(self) -> None:
        utm = CRS.from_epsg(31981)  # SIRGAS 2000 / UTM 21S
        xs, ys = transform(CRS.from_epsg(4326), utm, [-55.8, -55.5], [-12.6, -12.3])
        caminho = self._json(
            "utm.geojson",
            {
                "type": "Feature",
                "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::31981"}},
                "properties": {},
                "geometry": _poligono(min(xs), min(ys), max(xs), max(ys)),
            },
        )

        area = importar_area(caminho)

        self.assertEqual(area.crs_origem, "EPSG:31981")
        self.assertAlmostEqual(area.bbox[0], -55.8, delta=0.01)
        self.assertAlmostEqual(area.bbox[1], -12.6, delta=0.01)
        self.assertAlmostEqual(area.bbox[2], -55.5, delta=0.01)
        self.assertAlmostEqual(area.bbox[3], -12.3, delta=0.01)
        # O retângulo reprojetado nunca fica menor que a área original.
        self.assertLessEqual(area.bbox[0], -55.8 + 1e-6)
        self.assertGreaterEqual(area.bbox[2], -55.5 - 1e-6)
        self.assertIn("convertidas de EPSG:31981", " ".join(area.avisos))

    def test_rejeita_ponto_sem_area(self) -> None:
        caminho = self._json("ponto.geojson", {"type": "Point", "coordinates": [-55.0, -12.0]})

        with self.assertRaisesRegex(ValueError, "não tem área"):
            importar_area(caminho)

    def test_rejeita_coordenadas_fora_do_intervalo_geografico(self) -> None:
        caminho = self._json("errado.geojson", _poligono(500000, 8600000, 510000, 8610000))

        with self.assertRaisesRegex(ValueError, "Longitudes"):
            importar_area(caminho)

    def test_rejeita_json_sem_geometria_e_formato_desconhecido(self) -> None:
        with self.assertRaisesRegex(ValueError, "não contém geometrias"):
            importar_area(self._json("vazio.geojson", {"type": "FeatureCollection", "features": []}))
        outro = self.pasta / "area.kml"
        outro.write_text("<kml/>", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Formato não suportado"):
            importar_area(outro)

    def test_shapefile_geografico_le_cabecalho_e_prj(self) -> None:
        shp = self.pasta / "limite.shp"
        _escrever_shp(shp, (-56.0, -13.0, -55.0, -12.0))
        shp.with_suffix(".prj").write_text(CRS.from_epsg(4674).to_wkt(), encoding="utf-8")

        area = importar_area(shp)

        self.assertEqual(area.nome, "limite")
        self.assertEqual(area.crs_origem, "EPSG:4674")
        # SIRGAS 2000 e WGS 84 diferem em centímetros; o retângulo é reprojetado.
        self.assertAlmostEqual(area.bbox[0], -56.0, places=3)
        self.assertAlmostEqual(area.bbox[3], -12.0, places=3)

    def test_shapefile_projetado_e_reprojetado(self) -> None:
        utm = CRS.from_epsg(31981)
        xs, ys = transform(CRS.from_epsg(4326), utm, [-55.8, -55.5], [-12.6, -12.3])
        shp = self.pasta / "utm.shp"
        _escrever_shp(shp, (min(xs), min(ys), max(xs), max(ys)))
        shp.with_suffix(".prj").write_text(utm.to_wkt(), encoding="utf-8")

        area = importar_area(shp)

        self.assertEqual(area.crs_origem, "EPSG:31981")
        self.assertAlmostEqual(area.bbox[0], -55.8, delta=0.01)
        self.assertAlmostEqual(area.bbox[3], -12.3, delta=0.01)

    def test_shapefile_sem_prj_ou_invalido_e_rejeitado(self) -> None:
        shp = self.pasta / "sem_prj.shp"
        _escrever_shp(shp, (-56.0, -13.0, -55.0, -12.0))
        with self.assertRaisesRegex(ValueError, r"\.prj"):
            importar_area(shp)

        invalido = self.pasta / "invalido.shp"
        invalido.write_bytes(b"x" * 100)
        with self.assertRaisesRegex(ValueError, "não é um Shapefile"):
            importar_area(invalido)

        vazio = self.pasta / "vazio.shp"
        _escrever_shp(vazio, (0, 0, 0, 0), tipo=0)
        with self.assertRaisesRegex(ValueError, "não contém geometrias"):
            importar_area(vazio)

    def test_bbox_de_geojson_conta_geometrias(self) -> None:
        bbox, total = bbox_de_geojson(
            {
                "type": "GeometryCollection",
                "geometries": [_poligono(-56, -13, -55.5, -12.5), _poligono(-55.4, -12.4, -55, -12)],
            }
        )

        self.assertEqual(bbox, (-56, -13, -55, -12))
        self.assertEqual(total, 2)


class TestMunicipios(TestCase):
    MUNICIPIOS = [
        Municipio("5107925", "Sorriso", "MT", (-56.3, -13.1, -55.2, -12.0)),
        Municipio("5103403", "Cuiabá", "MT", (-56.3, -15.7, -55.4, -15.0)),
        Municipio("5107602", "Rondonópolis", "MT", (-55.1, -16.9, -54.3, -16.2)),
        Municipio("5106224", "Nova Mutum", "MT", (-56.9, -14.2, -55.6, -13.0)),
        Municipio("5107206", "Vila Rica", "MT", (-51.5, -10.4, -50.6, -9.6)),
        Municipio("5107180", "Ribeirão Cascalheira", "MT", (-52.3, -13.2, -51.2, -11.8)),
    ]

    def test_normaliza_acentos_e_caixa(self) -> None:
        self.assertEqual(normalizar_texto("  CUIABÁ  "), "cuiaba")

    def test_busca_sem_acento_prioriza_prefixo(self) -> None:
        resultado = buscar_municipios(self.MUNICIPIOS, "cuiaba")
        self.assertEqual([m.nome for m in resultado], ["Cuiabá"])

        resultado = buscar_municipios(self.MUNICIPIOS, "mu")
        self.assertEqual([m.nome for m in resultado], ["Nova Mutum"])

        resultado = buscar_municipios(self.MUNICIPIOS, "ri")
        self.assertEqual(
            [m.nome for m in resultado], ["Ribeirão Cascalheira", "Sorriso", "Vila Rica"]
        )

    def test_termo_curto_nao_busca(self) -> None:
        self.assertEqual(buscar_municipios(self.MUNICIPIOS, "s"), [])

    def test_carrega_arquivo_e_ignora_itens_invalidos(self) -> None:
        with TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "municipios.json"
            caminho.write_text(
                json.dumps(
                    {
                        "municipios": [
                            {"codigo_ibge": "1", "nome": "A", "uf": "mt", "bbox": [-56, -13, -55, -12]},
                            {"codigo_ibge": "2", "nome": "B", "uf": "MT", "bbox": [0, 0, 0, 0]},
                            {"nome": "C"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            municipios = carregar_municipios(caminho)

        self.assertEqual(len(municipios), 1)
        self.assertEqual(municipios[0].uf, "MT")
        self.assertEqual(carregar_municipios(Path("/nao/existe.json")), [])

    def test_ferramenta_ibge_combina_nomes_e_malha_sem_rede(self) -> None:
        especificacao = importlib.util.spec_from_file_location(
            "gerar_municipios_ibge", RAIZ / "tools" / "gerar_municipios_ibge.py"
        )
        modulo = importlib.util.module_from_spec(especificacao)
        especificacao.loader.exec_module(modulo)
        nomes = [{"id": 5107925, "nome": "Sorriso"}, {"id": 5103403, "nome": "Cuiabá"}]
        malha = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"codarea": "5107925"}, "geometry": _poligono(-56.3, -13.1, -55.2, -12.0)},
                {"type": "Feature", "properties": {"codarea": "5103403"}, "geometry": _poligono(-56.3, -15.7, -55.4, -15.0)},
            ],
        }

        lista, sem_malha = modulo.montar_lista("mt", nomes, malha)

        self.assertEqual([item["nome"] for item in lista], ["Cuiabá", "Sorriso"])
        self.assertEqual(lista[1]["bbox"], [-56.3, -13.1, -55.2, -12.0])
        self.assertEqual(lista[0]["uf"], "MT")
        self.assertEqual(sem_malha, [])
        parcial, sem_malha = modulo.montar_lista("MT", nomes, {"features": malha["features"][:1]})
        self.assertEqual([item["nome"] for item in parcial], ["Sorriso"])
        self.assertEqual(sem_malha, [{"codigo_ibge": "5103403", "nome": "Cuiabá", "uf": "MT"}])
        with self.assertRaisesRegex(ValueError, "não trouxe municípios"):
            modulo.montar_lista("MT", nomes, {"features": []})
