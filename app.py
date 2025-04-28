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
from codigos_hidro import indice_spi, calculo_precipitacoes, problema_inverso_idf, save_figure_temp

st.set_page_config(page_title="Análise de Estações BDMEP", layout="wide")
st.title("Análise de Estações BDMET")

# Inicializa session_state
if "df_resumo" not in st.session_state:
    st.session_state.df_resumo = None
if "planilhas_completas" not in st.session_state:
    st.session_state.planilhas_completas = {}
if "processed_zip" not in st.session_state:
    st.session_state.processed_zip = False

uploaded_zip = st.file_uploader("Envie o arquivo .zip com as planilhas de estações:", type="zip")

if uploaded_zip is None and st.session_state.processed_zip:
    st.session_state.df_resumo = None
    st.session_state.planilhas_completas = {}
    st.session_state.processed_zip = False
    if "buffer_resumo" in st.session_state:
        del st.session_state.buffer_resumo
    if "ultima_selecao" in st.session_state:
        del st.session_state.ultima_selecao
    if "df_final" in st.session_state:
        del st.session_state.df_final
    if "buffer_final" in st.session_state:
        del st.session_state.buffer_final

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

                df_dados = pd.read_csv(file_path, sep=";", encoding="utf-8", skiprows=9)
                # print(df_dados.columns.tolist()) 
                cod = cabecalho.get("codigo_estacao", file)
                planilhas_completas[cod] = df_dados

                # Calcula porcentagem de falhas (valores nulos) nas colunas selecionadas
                total_linhas = len(df_dados)
                def calc_falha_percent(col):
                    return (df_dados[col].isna().sum() / total_linhas * 100) if col in df_dados.columns else None

                falha_precipitacao = calc_falha_percent("PRECIPITACAO TOTAL, DIARIO (AUT)(mm)")
                falha_temperatura = calc_falha_percent("TEMPERATURA MEDIA, DIARIA (AUT)(°C)")
                falha_umidade = calc_falha_percent("UMIDADE RELATIVA DO AR, MEDIA DIARIA (AUT)(%)")
                falha_vento = calc_falha_percent("VENTO, VELOCIDADE MEDIA DIARIA (AUT)(m/s)")

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
                    "falha de precipitação (%)": falha_precipitacao,
                    "falha de temperatura média (%)": falha_temperatura,
                    "falha de umidade relativa (%)": falha_umidade,
                    "falha de velocidade do vento (%)": falha_vento
                })

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

        filtro_altitude = st.slider(
            "Filtrar por altitude (m):",
            min_value=alt_min,
            max_value=alt_max,
            value=(alt_min, alt_max)
        )

    # --- Aplica os filtros no DataFrame
    df_filtrado = df_resumo.copy()

    if filtro_nome != "Todos":
        # Extrai só o nome da cidade da opção selecionada
        cidade_nome = filtro_nome.split(' (')[0]
        cod_estacao = filtro_nome.split('(')[-1].replace(')', '')
        df_filtrado = df_filtrado[
            (df_filtrado["nome"] == cidade_nome) &
            (df_filtrado["codigo_estacao"] == cod_estacao)
        ]

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

    # Função para definir a cor baseada na situação
    def cor_situacao(situacao):
        situacao = situacao.lower()
        if situacao == "operante":
            return "green"
        elif situacao == "desativada":
            return "red"
        elif situacao == "pane":
            return "orange"
        elif situacao == "fechada":
            return "darkblue"
        else:
            return "gray"  # qualquer outra situação

    for _, row in gdf.iterrows():
        cor = cor_situacao(row["situacao"])
        folium.CircleMarker(
            location=[row["latitude"], row["longitude"]],
            radius=5,
            color=cor,
            fill=True,
            fill_opacity=0.8,
            fill_color=cor,
            tooltip=f"{row['nome']} ({row['codigo_estacao']}) - {row['situacao']}",
            popup=f"{row['nome']} ({row['codigo_estacao']})\nSituação: {row['situacao']}"
        ).add_to(m)

    st_folium(m, width=1500, height=500)



    # --- SPI e IDF para a cidade selecionada ---
    st.subheader("Análise SPI e Curva IDF para a cidade selecionada")

    if filtro_nome != "Todos":
        try:
            # Extrai código da estação e nome da cidade do filtro
            cod_estacao = filtro_nome.split("(")[-1].replace(")", "").strip()
            nome_cidade = filtro_nome.split("(")[0].strip()

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

                        # Cria o gráfico
                        fig, ax = plt.subplots(figsize=(12, 4))
                        ax.plot(spi_df["AnoMes"].astype(str), spi_df["SPI"], marker="o", linestyle="-")
                        ax.axhline(0, color="black", linestyle="--")
                        ax.set_title(f"SPI - {filtro_nome}")
                        ax.set_ylabel("SPI")
                        ax.set_xticks(range(0, len(spi_df), max(1, len(spi_df) // 12)))
                        ax.set_xticklabels(spi_df["AnoMes"].astype(str)[::max(1, len(spi_df) // 12)], rotation=45)
                        fig.tight_layout()

                        # Salva o gráfico no buffer ANTES de exibir
                        fig_spi_bytes = io.BytesIO()
                        fig.savefig(fig_spi_bytes, format='png', bbox_inches='tight')
                        fig_spi_bytes.seek(0)

                        # Exibe no Streamlit
                        if not spi_df.empty:
                            st.pyplot(fig)
                        else:
                            st.warning("O gráfico SPI não pôde ser gerado. Verifique se há dados disponíveis para a estação.")

                        st.markdown("#### Estatísticas por mês (SPI)")
                        st.table(estatisticas_spi.reset_index(drop=True))

                    except Exception as e:
                        st.error(f"Erro ao calcular SPI: {e}")
                else:
                    st.warning("Colunas de Data ou Precipitação não foram encontradas na estação selecionada.")

                # --- Cálculo IDF ---
                try:
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

            # --- Gerar botão de download do pacote SPI + IDF ---
            st.write("")

            try:
                buffer_zip = io.BytesIO()

                with zipfile.ZipFile(buffer_zip, "w") as zip_file:
                    # 1. Gráfico do SPI
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
                    file_name=f"analise_spi_idf_{filtro_nome.replace(' ', '_').replace('(', '').replace(')', '')}.zip",
                    mime="application/zip"
                )

            except Exception as e:
                st.error(f"Erro ao gerar arquivos para download: {e}")

        except Exception as e:
            st.error(f"Erro ao processar a cidade selecionada: {e}")

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

        st.dataframe(st.session_state.df_final.head())


        st.download_button(
            "Baixar dados das estações selecionadas",
            data=st.session_state.buffer_final,
            file_name="dados_selecionados.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
