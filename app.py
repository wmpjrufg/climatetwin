import zipfile
import os
import tempfile
import zipfile
import folium
import io

import geopandas as gpd
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

from streamlit_folium import st_folium
from codigos_hidro import indice_spi, calculo_precipitacoes, problema_inverso_idf, save_figure_temp


st.set_page_config(page_title="Análise de Estações BDMET", layout="wide")
st.title("Análise de Estações BDMET")

# Inicializa session_state
if "df_resumo" not in st.session_state:
    st.session_state.df_resumo = None
if "planilhas_completas" not in st.session_state:
    st.session_state.planilhas_completas = {}
if "processed_zip" not in st.session_state:
    st.session_state.processed_zip = False

uploaded_zip = st.file_uploader("Envie o arquivo .zip com as planilhas de estações:", type="zip")

# Processa o zip apenas 1x
if uploaded_zip and not st.session_state.processed_zip:
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "uploaded.zip")
        with open(zip_path, "wb") as f:
            f.write(uploaded_zip.read())

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(tmpdir)

        folders = [f for f in os.scandir(tmpdir) if f.is_dir()]
        folder_path = folders[0].path if folders else tmpdir

        st.success(f"Pasta processada: `{os.path.basename(folder_path)}`")

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

                resumo.append({
                    "arquivo": file,
                    "nome": cabecalho.get("nome", ""),
                    "codigo_estacao": cabecalho.get("codigo_estacao", ""),
                    "latitude": float(cabecalho.get("latitude", 0)),
                    "longitude": float(cabecalho.get("longitude", 0)),
                    "altitude": float(cabecalho.get("altitude", 0)),
                    "situacao": cabecalho.get("situacao", ""),
                    "data_inicial": cabecalho.get("data_inicial", ""),
                    "data_final": cabecalho.get("data_final", "")
                })

                df_dados = pd.read_csv(file_path, sep=";", encoding="utf-8", skiprows=9)
                cod = cabecalho.get("codigo_estacao", file)
                planilhas_completas[cod] = df_dados

            except Exception as e:
                st.error(f"Erro ao processar {file}: {e}")

        st.session_state.df_resumo = pd.DataFrame(resumo)
        st.session_state.planilhas_completas = planilhas_completas
        st.session_state.processed_zip = True

if uploaded_zip and st.session_state.df_resumo is not None:
    df_resumo = st.session_state.df_resumo
    planilhas_completas = st.session_state.planilhas_completas

    st.subheader("Resumo das Estações")
    st.dataframe(df_resumo)

    # Gera e armazena o resumo apenas 1x
    if "buffer_resumo" not in st.session_state:
        buffer_resumo = io.BytesIO()
        df_resumo.to_excel(buffer_resumo, index=False)
        buffer_resumo.seek(0)
        st.session_state.buffer_resumo = buffer_resumo

    st.download_button(
        "Baixar resumo (.xlsx)",
        data=st.session_state.buffer_resumo,
        file_name="resumo_estacoes.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # --- Mapa com Folium
    st.subheader("Mapa das Estações")
    st.write("Filtros para o Mapa")

    col1, col2, col3 = st.columns(3)

    with col1:
        estados = sorted(df_resumo['nome'].unique())
        filtro_nome = st.selectbox("Filtrar por cidade (opcional):", ["Todos"] + estados)

    with col2:
        situacoes = df_resumo["situacao"].dropna().unique()
        filtro_situacao = st.multiselect("Situação da estação:", situacoes, default=situacoes)

    with col3:
        alt_min = float(df_resumo["altitude"].min())
        alt_max = float(df_resumo["altitude"].max())
        filtro_altitude = st.slider("Filtrar por altitude (m):", min_value=alt_min, max_value=alt_max,
                                    value=(alt_min, alt_max))

    # Aplica os filtros no DataFrame
    df_filtrado = df_resumo.copy()

    if filtro_nome != "Todos":
        df_filtrado = df_filtrado[df_filtrado["nome"] == filtro_nome]

    if filtro_situacao:
        df_filtrado = df_filtrado[df_filtrado["situacao"].isin(filtro_situacao)]

    df_filtrado = df_filtrado[
        (df_filtrado["altitude"] >= filtro_altitude[0]) &
        (df_filtrado["altitude"] <= filtro_altitude[1])
    ]

    # --- Mapa com Folium
    st.subheader(f"Mapa das Estações Filtradas ({len(df_filtrado)} encontradas)")

    df_geo = df_filtrado.dropna(subset=["latitude", "longitude"])
    df_geo["latitude"] = pd.to_numeric(df_geo["latitude"], errors="coerce")
    df_geo["longitude"] = pd.to_numeric(df_geo["longitude"], errors="coerce")
    gdf = gpd.GeoDataFrame(df_geo, geometry=gpd.points_from_xy(df_geo.longitude, df_geo.latitude), crs="EPSG:4326")

    m = folium.Map(location=[-15, -55], zoom_start=4)

    for _, row in gdf.iterrows():
        cor = "green" if row["situacao"].lower() == "operante" else "red"
        folium.CircleMarker(
            location=[row["latitude"], row["longitude"]],
            radius=5,
            color=cor,
            fill=True,
            fill_opacity=0.8,
            fill_color=cor,
            tooltip=row["codigo_estacao"],
            popup=f"{row['nome']} ({row['situacao']})"
        ).add_to(m)

    st_folium(m, width=1500, height=500)

    # --- SPI e IDF para a cidade selecionada ---
    st.subheader("Análise SPI e Curva IDF para a cidade selecionada")

    if filtro_nome != "Todos":
        cod_estacao = df_resumo[df_resumo["nome"] == filtro_nome]["codigo_estacao"].values[0]
        df_estacao = planilhas_completas.get(cod_estacao)

        if df_estacao is not None:
            df_spi = df_estacao.copy()

            # Tenta encontrar automaticamente as colunas relevantes
            col_data = next((col for col in df_spi.columns if "data" in col.lower()), None)
            col_prec = next((col for col in df_spi.columns if "precip" in col.lower()), None)

            if col_data and col_prec:
                df_spi = df_spi[[col_data, col_prec]]
                df_spi.columns = ['Data Medição', 'Precipitação Total Diária (mm)']

                try:
                    spi_df, estatisticas_spi = indice_spi(df_spi)

                    st.markdown("### Índice de Precipitação Padronizado (SPI)")
                    plt.figure(figsize=(12, 4))
                    plt.plot(spi_df["AnoMes"].astype(str), spi_df["SPI"], marker="o", linestyle="-")
                    plt.axhline(0, color="black", linestyle="--")
                    plt.title(f"SPI - {filtro_nome}")
                    plt.ylabel("SPI")
                    plt.xticks(
                        ticks=range(0, len(spi_df), max(1, len(spi_df) // 12)),
                        labels=spi_df["AnoMes"].astype(str)[::max(1, len(spi_df) // 12)],
                        rotation=45
                    )
                    plt.tight_layout()
                    st.pyplot(plt)

                    st.markdown("#### Estatísticas por mês (SPI)")
                    st.table(estatisticas_spi.reset_index(drop=True))

                except Exception as e:
                    st.error(f"Erro ao calcular SPI: {e}")
            else:
                st.warning("Colunas de Data ou Precipitação não foram encontradas na estação selecionada.")

            # --- Cálculo IDF ---
            try:
                # st.markdown("### Curva IDF (Intensidade x Duração x Frequência)")
                hmax_df, preciptacao_df, intensidade_df, df_longo, media, desvio = calculo_precipitacoes(df_estacao)
                a, b, c, d = problema_inverso_idf(df_longo)
                
                st.write("")
                st.markdown(f"**Parâmetros IDF (ajustados via otimização):**")
                st.markdown(f"- a = `{a:.4f}`")
                st.markdown(f"- b = `{b:.4f}`")
                st.markdown(f"- c = `{c:.4f}`")
                st.markdown(f"- d = `{d:.4f}`")

                equacao_idf = (
                    r"I = \frac{{{:.2f} \cdot T_r^{{{:.2f}}}}}{{(t + {:.2f})^{{{:.2f}}}}}"
                    .format(a, b, c, d)
                )
                st.latex(equacao_idf)

            except Exception as e:
                st.error(f"Erro ao calcular curva IDF: {e}")
        else:
            st.warning("Estação selecionada não possui dados disponíveis.")

        # Gerar botão de download do pacote SPI + IDF
        st.write("")

        try:
            buffer_zip = io.BytesIO()

            with zipfile.ZipFile(buffer_zip, "w") as zip_file:
                # 1. Gráfico do SPI
                fig_spi_bytes = io.BytesIO()
                fig_spi.savefig(fig_spi_bytes, format='png', bbox_inches='tight')
                fig_spi_bytes.seek(0)
                zip_file.writestr("spi_grafico.png", fig_spi_bytes.read())

                # 2. Tabela SPI
                buffer_excel = io.BytesIO()
                estatisticas_spi.to_excel(buffer_excel, index=False)
                buffer_excel.seek(0)
                zip_file.writestr("estatisticas_spi.xlsx", buffer_excel.read())

                # 3. Parâmetros IDF
                txt_idf = f"""Parâmetros IDF ajustados para {filtro_nome}:

        a = {a:.6f}
        b = {b:.6f}
        c = {c:.6f}
        d = {d:.6f}
        """
                zip_file.writestr("parametros_idf.txt", txt_idf)

            buffer_zip.seek(0)

            st.download_button(
                label="Download dos resultados SPI + IDF",
                data=buffer_zip,
                file_name=f"analise_spi_idf_{filtro_nome}.zip",
                mime="application/zip"
            )

        except Exception as e:
            st.error(f"Erro ao gerar arquivos para download: {e}")

    else:
        st.info("Selecione uma cidade para visualizar a análise SPI e IDF.")


    # --- Seleção e download dos dados completos
    st.subheader("Exportar Dados")
    selecao = st.multiselect("Escolha pelo código da estação:", df_resumo["codigo_estacao"].dropna().unique())

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

        st.dataframe(st.session_state.df_final.head())

        st.download_button(
            "Baixar dados das estações selecionadas",
            data=st.session_state.buffer_final,
            file_name="dados_selecionados.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
