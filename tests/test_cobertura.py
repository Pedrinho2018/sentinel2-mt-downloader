from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform as transformar

from sentinel2_mt.cobertura.amostragem import (
    AmostradorJanelas,
    OpcoesAmostragem,
    ResultadoAmostra,
    deslocamento_radiometrico,
    janela_dentro_da_pegada,
    qualidade_scl,
    rgb_para_rotulagem,
)
from sentinel2_mt.cobertura.grade import (
    Celula,
    Regiao,
    alocar_por_regiao,
    carregar_estado,
    carregar_regioes,
    criar_grade,
    distancia_km,
    ordenar_por_regiao_intercalada,
    selecionar_celulas,
)
from sentinel2_mt.cobertura.registro import (
    RegistroCobertura,
    estimativas,
    executar_lote,
    gerar_mapa,
    gravar_catalogo,
)
from sentinel2_mt.laboratorio.datasets import _recortes_da_pasta, tile_de


def quadrado(x0, y0, x1, y1):
    return ((((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)),),)


ESTADO = Regiao("51", "Teste", 100.0, quadrado(-56, -14, -54, -12))
OESTE = Regiao("1", "Oeste", 50.0, quadrado(-56, -14, -55, -12))
LESTE = Regiao("2", "Leste", 50.0, quadrado(-55, -14, -54, -12))


class TestGrade(TestCase):
    def test_ponto_em_poligono_respeita_buracos(self) -> None:
        externo = ((-56, -14), (-54, -14), (-54, -12), (-56, -12), (-56, -14))
        buraco = ((-55.5, -13.5), (-54.5, -13.5), (-54.5, -12.5), (-55.5, -12.5), (-55.5, -13.5))
        regiao = Regiao("x", "x", 1, ((externo, buraco),))
        self.assertTrue(regiao.contem(-55.8, -13.8))
        self.assertFalse(regiao.contem(-55.0, -13.0))
        self.assertFalse(regiao.contem(-53.0, -13.0))

    def test_grade_atribui_regiao_pelo_centro(self) -> None:
        celulas = criar_grade([OESTE, LESTE], ESTADO, 0.5)
        self.assertEqual(len(celulas), 16)
        self.assertEqual({c.regiao_nome for c in celulas if c.lon < -55}, {"Oeste"})
        self.assertEqual(len({c.id for c in celulas}), 16)

    def test_alocacao_minimo_e_proporcional(self) -> None:
        grande = Regiao("a", "A", 300.0, ESTADO.poligonos)
        pequena = Regiao("b", "B", 100.0, ESTADO.poligonos)
        self.assertEqual(alocar_por_regiao([grande, pequena], 20, 5), {"a": 13, "b": 7})
        with self.assertRaises(ValueError):
            alocar_por_regiao([grande, pequena], 8, 5)

    def test_selecao_respeita_distancia_e_e_reproduzivel(self) -> None:
        celulas = criar_grade([OESTE, LESTE], ESTADO, 0.25)
        a = selecionar_celulas(celulas, {"1": 4, "2": 4}, distancia_minima_km=40, semente=1)
        b = selecionar_celulas(celulas, {"1": 4, "2": 4}, distancia_minima_km=40, semente=1)
        self.assertEqual([c.id for c in a], [c.id for c in b])
        for i, x in enumerate(a):
            for y in a[i + 1:]:
                self.assertGreaterEqual(distancia_km(x.lon, x.lat, y.lon, y.lat), 40)
        intercaladas = ordenar_por_regiao_intercalada(a)
        self.assertNotEqual(intercaladas[0].regiao_codigo, intercaladas[1].regiao_codigo)

    def test_limites_oficiais_de_mt(self) -> None:
        regioes = carregar_regioes()
        self.assertEqual(sorted(r.nome for r in regioes), ["Barra do Garças", "Cuiabá", "Cáceres", "Rondonópolis", "Sinop"])
        self.assertAlmostEqual(sum(r.area_km2 for r in regioes), carregar_estado().area_km2, delta=1)
        self.assertEqual([r.nome for r in regioes if r.contem(-55.5, -12.5)], ["Sinop"])


class TestRadiometria(TestCase):
    def test_offset_pela_baseline(self) -> None:
        self.assertEqual(deslocamento_radiometrico("S2C_MSIL2A_20260914T134701_N0512_R024_T21LYD_x"), 1000)
        self.assertEqual(deslocamento_radiometrico("S2A_MSIL2A_20210101T134701_N0300_R024_T21LYD_x"), 0)
        self.assertEqual(deslocamento_radiometrico("sem_baseline"), 0)

    def test_png_remove_offset_e_preserva_nodata(self) -> None:
        dados = np.array([[0, 1000, 2000, 3000]], dtype=np.uint16)
        rgb = rgb_para_rotulagem(dados, dados, dados, 1000, 0, 2000)
        self.assertEqual(rgb[0, :, 0].tolist(), [0, 0, 128, 255])

    def test_qualidade_scl(self) -> None:
        scl = np.array([[0, 4, 4, 9], [3, 5, 6, 4]], dtype=np.uint8)
        nuvem, validos = qualidade_scl(scl)
        self.assertAlmostEqual(nuvem, 100 * 2 / 7)
        self.assertAlmostEqual(validos, 100 * 7 / 8)
        self.assertEqual(qualidade_scl(np.zeros((2, 2), np.uint8)), (100.0, 0.0))

    def test_pegada_contem_janela(self) -> None:
        item = SimpleNamespace(geometry={"type": "Polygon", "coordinates": [list(quadrado(-56, -14, -55, -13)[0][0])]})
        self.assertTrue(janela_dentro_da_pegada(item, -55.5, -13.5, 5120))
        self.assertFalse(janela_dentro_da_pegada(item, -55.01, -13.5, 5120))
        self.assertTrue(janela_dentro_da_pegada(SimpleNamespace(geometry=None), 0, 0, 5120))


class _Busca:
    def __init__(self, itens): self.itens = itens
    def items(self): return iter(self.itens)


class ClienteFalso:
    def __init__(self, itens): self.itens = itens; self.pedidos = []
    def search(self, **kwargs): self.pedidos.append(kwargs); return _Busca(self.itens)


class TestAmostrador(TestCase):
    """Cenas sintéticas locais (UTM 21S), lidas pelo mesmo caminho do rasterio usado nos COGs."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        self.lon, self.lat = -55.5, -13.0
        xs, ys = transformar("EPSG:4326", "EPSG:32721", [self.lon], [self.lat])
        self.x0, self.y0 = round(xs[0]) - 5000, round(ys[0]) + 5000  # cena de 10 km × 10 km

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def cena(self, nome: str, scl_valor: int, data: str, nuvem_cena: float = 1.0):
        pasta = self.raiz / "cenas" / nome
        pasta.mkdir(parents=True)
        hrefs = {}
        for banda, valor in (("B02", 1300), ("B03", 1600), ("B04", 1900)):
            caminho = pasta / f"{banda}.tif"
            with rasterio.open(caminho, "w", driver="GTiff", width=1000, height=1000, count=1, dtype="uint16",
                               crs="EPSG:32721", transform=from_origin(self.x0, self.y0, 10, 10)) as r:
                r.write(np.full((1000, 1000), valor, np.uint16), 1)
            hrefs[banda] = SimpleNamespace(href=str(caminho))
        caminho = pasta / "SCL.tif"
        with rasterio.open(caminho, "w", driver="GTiff", width=500, height=500, count=1, dtype="uint8",
                           crs="EPSG:32721", transform=from_origin(self.x0, self.y0, 20, 20)) as r:
            r.write(np.full((500, 500), scl_valor, np.uint8), 1)
        hrefs["SCL"] = SimpleNamespace(href=str(caminho))
        return SimpleNamespace(id=nome, datetime=datetime.fromisoformat(data).replace(tzinfo=timezone.utc),
                               properties={"eo:cloud_cover": nuvem_cena, "datetime": data}, assets=hrefs, geometry=None)

    def test_recusa_janela_nublada_e_aceita_data_alternativa(self) -> None:
        nublada = self.cena("S2A_MSIL2A_20260801T000000_N0512_R001_T21LXG_A", 9, "2026-08-01", 0.5)
        limpa = self.cena("S2A_MSIL2A_20260815T000000_N0512_R001_T21LXG_B", 4, "2026-08-15", 2.0)
        cliente = ClienteFalso([limpa, nublada])
        amostrador = AmostradorJanelas(self.raiz / "saida", self.raiz, cliente)
        celula = Celula("r0c0", self.lon, self.lat, 0.25, "1", "Oeste")

        r = amostrador.amostrar(celula, OpcoesAmostragem(inicio="2026-06-01", fim="2026-09-30"))

        self.assertTrue(r.aceita)
        self.assertEqual([t.resultado.split()[0] for t in r.tentativas], ["nuvem/sombra", "aceita"])
        pasta = self.raiz / r.pasta
        with rasterio.open(pasta / "multiband.tif") as m:
            self.assertEqual(m.crs.to_string(), "EPSG:32721")
            self.assertEqual((m.width, m.height, m.dtypes[0], m.nodata), (512, 512, "uint16", 0))
            self.assertEqual(m.descriptions, ("B02", "B03", "B04"))
            self.assertEqual(int(m.read(3)[0, 0]), 1900)  # valor original preservado
            self.assertEqual(m.res, (10.0, 10.0))
        with rasterio.open(pasta / "scl.tif") as s:
            self.assertEqual((s.width, s.res), (256, (20.0, 20.0)))
            self.assertEqual(s.transform.c, m.transform.c)  # mesma origem: janelas alinhadas
        meta = json.loads((pasta / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["boa_add_offset"], -1000)
        self.assertEqual(meta["amostragem"]["regiao_intermediaria"]["nome"], "Oeste")
        self.assertEqual(tile_de(meta["scene_id"]), "21LXG")
        self.assertEqual(cliente.pedidos[0]["collections"], ["S2_L2A-1"])
        recortes = _recortes_da_pasta(self.raiz / "saida")
        self.assertEqual(len(recortes), 1)
        self.assertEqual(gravar_catalogo([r], self.raiz / "saida/patches.csv", self.raiz), 1)

    def test_sem_cena_e_janela_fora_registram_motivo(self) -> None:
        celula = Celula("r0c0", self.lon, self.lat, 0.25, "1", "Oeste")
        vazio = AmostradorJanelas(self.raiz / "s", self.raiz, ClienteFalso([]))
        self.assertIn("nenhuma cena", vazio.amostrar(celula, OpcoesAmostragem("2026-06-01", "2026-09-30")).tentativas[0].resultado)
        longe = Celula("r9c9", self.lon + 0.2, self.lat, 0.25, "1", "Oeste")
        cliente = ClienteFalso([self.cena("S2A_MSIL2A_20260815T000000_N0512_R001_T21LXG_C", 4, "2026-08-15")])
        r = AmostradorJanelas(self.raiz / "s", self.raiz, cliente).amostrar(longe, OpcoesAmostragem("2026-06-01", "2026-09-30"))
        self.assertFalse(r.aceita)
        self.assertIn("fora da cena", r.tentativas[0].resultado)


class AmostradorFalso:
    def __init__(self, recusar: set[str]): self.recusar = recusar; self.chamadas = []

    def amostrar(self, celula, _opcoes):
        self.chamadas.append(celula.id)
        aceita = celula.id not in self.recusar
        return ResultadoAmostra(celula, aceita, [], f"p_{celula.id}" if aceita else "", "", {"date": "2026-08-01"} if aceita else {})


class TestLote(TestCase):
    def test_cota_por_regiao_substitui_celulas_e_nao_repete(self) -> None:
        celulas = criar_grade([OESTE, LESTE], ESTADO, 0.25)
        recusadas = {c.id for c in celulas if c.regiao_codigo == "1"}
        recusadas = set(sorted(recusadas)[:10])
        with TemporaryDirectory() as tmp:
            registro = RegistroCobertura(Path(tmp) / "cobertura.json")
            falso = AmostradorFalso(recusadas)
            resultados = executar_lote(falso, celulas, {"1": 3, "2": 3}, None, registro, lote="t",
                                       distancia_minima_km=30, semente=3)
            aceitas = [r for r in resultados if r.aceita]
            self.assertEqual(sum(1 for r in aceitas if r.celula.regiao_codigo == "1"), 3)
            self.assertEqual(sum(1 for r in aceitas if r.celula.regiao_codigo == "2"), 3)
            self.assertEqual(len(falso.chamadas), len(set(falso.chamadas)))
            for i, a in enumerate(aceitas):
                for b in aceitas[i + 1:]:
                    self.assertGreaterEqual(distancia_km(a.celula.lon, a.celula.lat, b.celula.lon, b.celula.lat), 30)
            recarregado = RegistroCobertura(Path(tmp) / "cobertura.json")
            self.assertEqual(len(recarregado.amostradas()), 6)
            segundo = executar_lote(AmostradorFalso(set()), celulas, {"1": 1}, None, recarregado, lote="t2",
                                    distancia_minima_km=30, semente=3)
            self.assertNotIn(segundo[0].celula.id, {r.celula.id for r in resultados})
            resumo = recarregado.resumo_por_regiao(celulas)
            self.assertEqual(resumo["Leste"]["amostrada"], 3)

    def test_registro_nao_rebaixa_celula_amostrada(self) -> None:
        with TemporaryDirectory() as tmp:
            registro = RegistroCobertura(Path(tmp) / "c.json")
            celula = Celula("x", -55, -13, 0.25, "1", "Oeste")
            registro.registrar(ResultadoAmostra(celula, True, [], "p", "", {"date": "2026-08-01"}), "a")
            registro.registrar(ResultadoAmostra(celula, False), "b")
            self.assertEqual(registro.celulas["x"]["estado"], "amostrada")

    def test_estimativas_e_mapa(self) -> None:
        e = estimativas(1202, 1.6, 6.0, 0.8)
        self.assertEqual(e["armazenamento_gb_por_epoca"], round(1202 * 1.6 / 1024, 2))
        self.assertEqual(e["horas_por_epoca"], round(1202 / 0.8 * 6 / 3600, 1))
        with TemporaryDirectory() as tmp:
            destino = Path(tmp) / "mapa.png"
            gerar_mapa(ESTADO, [OESTE, LESTE], criar_grade([OESTE, LESTE], ESTADO, 0.5),
                       RegistroCobertura(Path(tmp) / "c.json"), destino, "teste", amostras=[(-55.2, -13.1)])
            self.assertGreater(destino.stat().st_size, 1000)
