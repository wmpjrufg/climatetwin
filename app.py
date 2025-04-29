import zipfile
import os
import tempfile
import folium
import io

import geopandas as gpd
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

from streamlit_folium import st_folium
from pathlib import Path
from io import BytesIO
from codigos_hidro import indice_spi, calculo_precipitacoes, problema_inverso_idf

st.set_page_config(page_title="Análise de Estações BDMEP", layout="wide")
st.title("Análise de Estações BDMEP")

caminho_arquivo = Path("ultima_sincro.txt")
if caminho_arquivo.exists():
    with open(caminho_arquivo, "r", encoding="utf-8") as f:
        data = f.read().strip()
    st.success(f"Data da última sincronização: {data}")
else:
    st.warning("Arquivo 'ultima_sincro.txt' não encontrado.")

@st.cache_data(show_spinner="Carregando dados do ZIP...")
def processar_zip(uploaded_zip_bytes):
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "uploaded.zip")
        with open(zip_path, "wb") as f:
            f.write(uploaded_zip_bytes.read())

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(tmpdir)

        folders = [f for f in os.scandir(tmpdir) if f.is_dir()]
        folder_path = folders[0].path if folders else tmpdir

        files = [f for f in os.listdir(folder_path) if f.endswith('.csv')]
        resumo = []
        planilhas_completas = {}

        for file in files:
            file_path = os.path.join(folder_path, file)
            try:
                with open(file_path, encoding='utf-8') as f:
                    linhas = [next(f).strip() for _ in range(9)]

                cabecalho = {}
                for linha in linhas:
                    if ':' in linha:
                        chave, valor = linha.split(':', 1)
                        cabecalho[chave.strip().lower().replace(' ', '_')] = valor.strip()

                df_dados = pd.read_csv(file_path, sep=";", encoding="utf-8", skiprows=9)
                cod = cabecalho.get("codigo_estacao", file)
                planilhas_completas[cod] = df_dados

                total_linhas = len(df_dados)
                def calc_falha_percent(col):
                    return (df_dados[col].isna().sum() / total_linhas * 100) if col in df_dados.columns else None

                resumo.append({
                    "arquivo": file,
                    "nome": cabecalho.get("nome", ""),
                    "codigo_estacao": cabecalho.get("codigo_estacao", ""),
                    "latitude": float(cabecalho.get("latitude", 0)),
                    "longitude": float(cabecalho.get("longitude", 0)),
                    "altitude": float(cabecalho.get("altitude", 0)),
                    "situacao": cabecalho.get("situacao", ""),
                    "data_inicial": cabecalho.get("data_inicial", ""),
                    "data_final": cabecalho.get("data_final", ""),
                    "falha de precipitação (%)": calc_falha_percent("PRECIPITACAO TOTAL, DIARIO (AUT)(mm)"),
                    "falha de temperatura média (%)": calc_falha_percent("TEMPERATURA MEDIA, DIARIA (AUT)(°C)"),
                    "falha de umidade relativa (%)": calc_falha_percent("UMIDADE RELATIVA DO AR, MEDIA DIARIA (AUT)(%)"),
                    "falha de velocidade do vento (%)": calc_falha_percent("VENTO, VELOCIDADE MEDIA DIARIA (AUT)(m/s)")
                })
            except Exception as e:
                st.error(f"Erro ao processar {file}: {e}")

        df_resumo = pd.DataFrame(resumo)
        return df_resumo, planilhas_completas, os.path.basename(folder_path)

@st.cache_data(show_spinner="Calculando SPI...")
def calcular_spi(df_spi):
    return indice_spi(df_spi)

@st.cache_data(show_spinner="Calculando curva IDF...")
def calcular_idf(df_estacao):
    hmax_df, preciptacao_df, intensidade_df, df_longo, media, desvio = calculo_precipitacoes(df_estacao)
    a, b, c, d = problema_inverso_idf(df_longo)
    return (hmax_df, preciptacao_df, intensidade_df, df_longo, media, desvio), (a, b, c, d)

# Leitura do arquivo fixo
df_resumo, planilhas_completas, nome_pasta = None, {}, None
caminho_fixo = "./BD/$2a$10$GPEMhanSCXsA4ZZn9M7nWe69AV4i79XA7TZa5j5VxeqGZrwNfvlC.zip"

with open(caminho_fixo, "rb") as f:
    uploaded_zip = BytesIO(f.read()) 

if uploaded_zip:
    df_resumo, planilhas_completas, nome_pasta = processar_zip(uploaded_zip)
    st.success(f"Pasta processada: `{nome_pasta}`")

if df_resumo is not None:
    st.subheader("Resumo das Estações")
    st.dataframe(df_resumo)

    buffer = io.BytesIO()
    df_resumo.to_excel(buffer, index=False)
    buffer.seek(0)

    st.download_button(
        "Baixar resumo (.xlsx)",
        data=buffer,
        file_name="resumo_estacoes.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    st.subheader("Mapa das Estações")
    col1, col2, col3 = st.columns(3)

    with col1:
        cidades_codigos = sorted([f"{nome} ({cod})" for nome, cod in zip(df_resumo['nome'], df_resumo['codigo_estacao'])])
        filtro_nome = st.selectbox("Filtrar por cidade (opcional):", ["Todos"] + cidades_codigos)

    with col2:
        situacoes = df_resumo["situacao"].dropna().unique()
        filtro_situacao = st.multiselect("Situação da estação:", situacoes, default=situacoes)

    with col3:
        alt_min = float(df_resumo["altitude"].min())
        alt_max = float(df_resumo["altitude"].max())
        if alt_min == alt_max:
            alt_min -= 1
            alt_max += 1
        filtro_altitude = st.slider("Filtrar por altitude (m):", min_value=alt_min, max_value=alt_max, value=(alt_min, alt_max))

    df_filtrado = df_resumo.copy()
    if filtro_nome != "Todos":
        cidade_nome = filtro_nome.split(' (')[0]
        cod_estacao = filtro_nome.split('(')[-1].replace(')', '')
        df_filtrado = df_filtrado[(df_filtrado["nome"] == cidade_nome) & (df_filtrado["codigo_estacao"] == cod_estacao)]

    if filtro_situacao:
        df_filtrado = df_filtrado[df_filtrado["situacao"].isin(filtro_situacao)]

    df_filtrado = df_filtrado[(df_filtrado["altitude"] >= filtro_altitude[0]) & (df_filtrado["altitude"] <= filtro_altitude[1])]

    st.subheader(f"Mapa das Estações Filtradas ({len(df_filtrado)} encontradas)")

    df_geo = df_filtrado.dropna(subset=["latitude", "longitude"])
    df_geo["latitude"] = pd.to_numeric(df_geo["latitude"], errors="coerce")
    df_geo["longitude"] = pd.to_numeric(df_geo["longitude"], errors="coerce")
    gdf = gpd.GeoDataFrame(df_geo, geometry=gpd.points_from_xy(df_geo.longitude, df_geo.latitude), crs="EPSG:4326")

    m = folium.Map(location=[-15, -55], zoom_start=4)

    def cor_situacao(situacao):
        situacao = situacao.lower()
        return {
            "operante": "green",
            "desativada": "red",
            "pane": "orange",
            "fechada": "darkblue"
        }.get(situacao, "gray")

    for _, row in gdf.iterrows():
        folium.CircleMarker(
            location=[row["latitude"], row["longitude"]],
            radius=5,
            color=cor_situacao(row["situacao"]),
            fill=True,
            fill_opacity=0.8,
            tooltip=f"{row['nome']} ({row['codigo_estacao']}) - {row['situacao']}"
        ).add_to(m)

    with st.container():
        st.markdown(
            """
            <style>
            .folium-map {
                height: 500px !important;
                overflow: hidden !important;
            }
            iframe {
                height: 500px !important;
                max-height: 500px !important;
            }
            </style>
            """,
            unsafe_allow_html=True
        )
        st_folium(m, width=1500, height=500)

    st.subheader("Análise SPI e Curva IDF para a cidade selecionada")

    if filtro_nome != "Todos":
        cod_estacao = filtro_nome.split("(")[-1].replace(")", "").strip()
        nome_cidade = filtro_nome.split("(")[0].strip()
        df_estacao = planilhas_completas.get(cod_estacao)

        if df_estacao is not None:
            col_data = next((col for col in df_estacao.columns if "data" in col.lower()), None)
            col_prec = next((col for col in df_estacao.columns if "precip" in col.lower()), None)

            if col_data and col_prec:
                df_spi = df_estacao[[col_data, col_prec]]
                df_spi.columns = ['Data Medição', 'Precipitação Total Diária (mm)']
                spi_df, estatisticas_spi = calcular_spi(df_spi)

                st.markdown("### Índice de Precipitação Padronizado (SPI)")
                fig, ax = plt.subplots(figsize=(12, 4))
                ax.plot(spi_df["AnoMes"].astype(str), spi_df["SPI"], marker="o")
                ax.axhline(0, color="black", linestyle="--")
                ax.set_title(f"SPI - {filtro_nome}")
                ax.set_ylabel("SPI")
                ax.set_xticks(range(0, len(spi_df), max(1, len(spi_df) // 12)))
                ax.set_xticklabels(spi_df["AnoMes"].astype(str)[::max(1, len(spi_df)//12)], rotation=45)
                fig.tight_layout()
                fig_spi_bytes = io.BytesIO()
                fig.savefig(fig_spi_bytes, format='png', bbox_inches='tight')
                fig_spi_bytes.seek(0)
                st.pyplot(fig)
                st.markdown("#### Estatísticas por mês (SPI)")
                st.table(estatisticas_spi.reset_index(drop=True))

                (hmax_df, preciptacao_df, intensidade_df, df_longo, media, desvio), (a, b, c, d) = calcular_idf(df_estacao)
                st.markdown(f"**Parâmetros IDF (ajustados via otimização):**\n- a = `{a:.4f}`\n- b = `{b:.4f}`\n- c = `{c:.4f}`\n- d = `{d:.4f}`")
                st.latex(rf"I = \frac{{{a:.2f} \cdot T_r^{{{b:.2f}}}}}{{(t + {c:.2f})^{{{d:.2f}}}}}")

                if st.button("Gerar pacote SPI + IDF"):
                    with st.spinner("Gerando pacote para download..."):
                        buffer_zip = io.BytesIO()
                        with zipfile.ZipFile(buffer_zip, "w") as zip_file:
                            fig_spi_bytes.seek(0)
                            zip_file.writestr("spi_grafico.png", fig_spi_bytes.read())
                            buffer_excel = io.BytesIO()
                            estatisticas_spi.to_excel(buffer_excel, index=False)
                            buffer_excel.seek(0)
                            zip_file.writestr("estatisticas_spi.xlsx", buffer_excel.read())
                            txt_idf = f"""Parâmetros IDF ajustados para {filtro_nome}:

a = {a:.6f}
b = {b:.6f}
c = {c:.6f}
d = {d:.6f}"""
                            zip_file.writestr("parametros_idf.txt", txt_idf)
                        buffer_zip.seek(0)
                        st.download_button(
                            label="Download dos resultados SPI + IDF",
                            data=buffer_zip,
                            file_name=f"analise_spi_idf_{filtro_nome.replace(' ', '_').replace('(', '').replace(')', '')}.zip",
                            mime="application/zip")
            else:
                st.warning("Colunas de Data ou Precipitação não foram encontradas na estação selecionada.")
        else:
            st.warning("Estação selecionada não possui dados disponíveis.")
    else:
        st.info("Selecione uma cidade para visualizar a análise SPI e IDF.")

    # --- Seleção e download dos dados completos
    st.subheader("Exportar Dados")
    df_ordenado = df_resumo.sort_values("codigo_estacao")
    opcoes_rotuladas = [f"{row['nome']} ({row['codigo_estacao']})" for _, row in df_ordenado.iterrows()]
    mapa_codigo_por_label = {f"{row['nome']} ({row['codigo_estacao']})": row['codigo_estacao'] for _, row in df_ordenado.iterrows()}
    selecao_rotulada = st.multiselect("Escolha a estação:", opcoes_rotuladas)
    selecao = [mapa_codigo_por_label[r] for r in selecao_rotulada if r in mapa_codigo_por_label]

    if selecao:
        if ("ultima_selecao" not in st.session_state) or (selecao != st.session_state.ultima_selecao):
            dfs = [planilhas_completas[cod] for cod in selecao if cod in planilhas_completas]
            df_final = pd.concat(dfs, ignore_index=True)
            buffer_final = io.BytesIO()
            df_final.to_excel(buffer_final, index=False)
            buffer_final.seek(0)

            st.session_state.df_final = df_final
            st.session_state.buffer_final = buffer_final
            st.session_state.ultima_selecao = selecao

        st.download_button(
            "Baixar dados das estações selecionadas",
            data=st.session_state.buffer_final,
            file_name="dados_selecionados.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )