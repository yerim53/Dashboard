"""
예제: 신규 권역 성장 기회
5주차 설계 예제의 쿼리를 SQLite로 바꾸고, 6주차 리포트의 KPI 카드 3개 · 차트 3장을
필터에 반응하도록 옮겨 둔 코드입니다. 

탭 3개(개요 · 탐색 · 액션) 뼈대가 들어 있습니다.
- ✏️표시: 내 5주차 · 6주차 결과물로 변경할 곳
- [블록 A] · [블록 B] · [블록 C]: Gemini에게 받은 블록으로 통째로 바꿀 곳
  (===== 시작 줄과 ===== 끝 줄 사이를 지우고 받은 블록을 붙임, 첫 줄은 맨 앞에 빈칸 없이)
"""
import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="배달 플랫폼 사용자 전환 및 이탈 분석", layout="wide")
st.title("배달 플랫폼 사용자 전환 및 이탈 분석")                                  # ✏️ 6주차 메인 제목
st.caption("경기 남부 신규 권역 | 2025.09~2026.08 원본 이벤트 · 가입 후 30일 고객 기준 대시보드")     # ✏️ 6주차 부제

# ------------------------------------------------------------
# 0. 필요한 데이터만 추출 — 쓸 테이블과 컬럼만 메모리 DB에 올리고, 정제 테이블을 처음 한 번만 만듦
# ------------------------------------------------------------
DATA_DIR = "data"                          # 원본 CSV를 넣어 둔 폴더
NEEDED = {                                 # ✏️ 이번 대시보드에 필요한 테이블과 컬럼 (쿼리에 쓰는 것만)
    "app_events": ["event_datetime", "customer_id", "session_id", "event_name",
                   "platform", "traffic_source", "campaign_name"],
    "orders": ["customer_id", "order_datetime"],
}

@st.cache_resource
def get_db():                              # 필요한 컬럼만 메모리 DB에 올리고, 정제 테이블 events·odr을 생성(처음 한 번)
    con = sqlite3.connect(":memory:", check_same_thread=False)
    for name, cols in NEEDED.items():
        # dtype=str: 고객 ID의 앞자리 0(0000002)이 숫자로 바뀌지 않게 글자로 읽음
        pd.read_csv(f"{DATA_DIR}/{name}.csv", usecols=cols, dtype=str).to_sql(name, con, index=False)
    # ✏️ 5주차 제외 규칙: 테스트 계정(T로 시작) 제외 · 이벤트 100건 이상인 비정상 세션 제외
    con.execute("""
        CREATE TABLE events AS
        SELECT *
          FROM app_events
         WHERE customer_id NOT LIKE 'T%'
           AND session_id NOT IN (SELECT session_id FROM app_events
                                   GROUP BY session_id HAVING COUNT(*) >= 100)
    """)
    con.execute("CREATE TABLE odr AS SELECT * FROM orders WHERE customer_id NOT LIKE 'T%'")
    # 고객별로 묶는 집계가 빨라지도록 customer_id에 인덱스를 붙임
    con.execute("CREATE INDEX idx_events_customer ON events(customer_id)")
    con.execute("CREATE INDEX idx_odr_customer ON odr(customer_id)")
    return con

# ------------------------------------------------------------
# 1. 데이터 준비 — 정제된 테이블을 SQL로 집계 (비율이 아니라 분자 · 분모 건수만)
# ------------------------------------------------------------
@st.cache_data
def query(sql):                            # SQL을 실행해 표로 돌려줌
    return pd.read_sql(sql, get_db())

# 고객별 가입일 · 가입 채널 · 플랫폼 (가입 이벤트 기준) - 두 쿼리 모두 사용
SQL_SIGNUP = """
su AS (
    SELECT customer_id,
           DATE(MIN(event_datetime))                    AS 가입일,
           MIN(COALESCE(campaign_name, traffic_source)) AS 가입채널,
           MIN(platform)                                AS 플랫폼
      FROM events
     WHERE event_name = 'sign_up'
     GROUP BY customer_id
)
"""

# ✏️ 가입월 × 가입 채널 × 플랫폼별 가입 후 30일 퍼널 (예제 Q1 · Q3)
SQL_SIGNUP_FUNNEL = f"""
WITH {SQL_SIGNUP},
ev AS (                                     -- 가입 후 30일 안의 행동
    SELECT DISTINCT e.customer_id, e.event_name
      FROM events e
      JOIN su ON e.customer_id = su.customer_id
     WHERE julianday(DATE(e.event_datetime)) - julianday(su.가입일) BETWEEN 0 AND 30
),
fo AS (                                     -- 고객별 첫 주문일
    SELECT customer_id, DATE(MIN(order_datetime)) AS 첫주문일
      FROM odr
     GROUP BY customer_id
)
SELECT strftime('%Y-%m', su.가입일) AS 가입월,
       su.가입채널,
       su.플랫폼,
       COUNT(DISTINCT su.customer_id)                                                         AS 가입자,
       COUNT(DISTINCT CASE WHEN ev.event_name = 'view_restaurant' THEN su.customer_id END)    AS 식당조회,
       COUNT(DISTINCT CASE WHEN ev.event_name = 'add_to_cart'     THEN su.customer_id END)    AS 장바구니,
       COUNT(DISTINCT CASE WHEN ev.event_name = 'begin_checkout'  THEN su.customer_id END)    AS 결제시작,
       COUNT(DISTINCT CASE WHEN julianday(fo.첫주문일) - julianday(su.가입일) BETWEEN 0 AND 30
                           THEN su.customer_id END)                                           AS 첫주문30일
  FROM su
  LEFT JOIN ev ON su.customer_id = ev.customer_id
  LEFT JOIN fo ON su.customer_id = fo.customer_id
 WHERE su.가입일 <= '2026-07-31'            -- 30일을 다 볼 수 있는 가입자만
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3
"""

# ✏️ 가입월 × 가입 채널 × 플랫폼별 30일 재주문 (예제 Q2, 분모가 첫 주문 고객이라 표를 따로 만듦)
SQL_REORDER = f"""
WITH {SQL_SIGNUP},
seq AS (
    SELECT customer_id, order_datetime,
           ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_datetime) AS 순번,
           LEAD(order_datetime) OVER (PARTITION BY customer_id ORDER BY order_datetime) AS 다음주문
      FROM odr
)
SELECT strftime('%Y-%m', su.가입일) AS 가입월,
       su.가입채널,
       su.플랫폼,
       COUNT(*)                                                                          AS 첫주문고객,
       SUM(CASE WHEN julianday(DATE(seq.다음주문)) - julianday(DATE(seq.order_datetime)) <= 30
                THEN 1 ELSE 0 END)                                                       AS 재주문30일
  FROM seq
  JOIN su ON seq.customer_id = su.customer_id
 WHERE seq.순번 = 1
   AND seq.order_datetime < '2026-08-01'    -- 30일을 다 볼 수 있는 첫 주문만
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3
"""

@st.cache_data
def load_data():
    return query(SQL_SIGNUP_FUNNEL), query(SQL_REORDER)

df, rd = load_data()                       # df: 가입 후 30일 퍼널 / rd: 30일 재주문

# 📌 AI 해설: 아래 AI_NOTES = {} 한 줄을 Gemini가 준 AI_NOTES = { … } 로 바꾸기
AI_NOTES = {}
AI_SOURCE = "🤖 Gemini가 미리 만든 해설 · 전체 기간 기준"



# ------------------------------------------------------------
# 2. 공통 색 / 서식 + 필터
# ------------------------------------------------------------
import plotly.graph_objects as go

NAVY = "#1F3A5F"
NAVY_LIGHT = "#486987"
GRAY = "#AAB3BE"
GRAY_LIGHT = "#D6DCE3"
INK = "#1E293B"
HIGHLIGHT = "organic"  # 6주차 주요 분석 집단. 강의의 Android 강조는 예제이므로 변경.

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap');
html, body, [class*="css"], [data-testid="stApp"], [data-testid="stSidebar"] {font-family:'Noto Sans KR', sans-serif;}
[data-testid="stMetricValue"] {color: #1F3A5F; font-weight: 700;}
[data-testid="stApp"] {background: #FFFFFF;}
h1,h2,h3 {letter-spacing: -.4px;}
</style>
""", unsafe_allow_html=True)


def style(fig, height=380, legend=True):
    fig.update_layout(
        height=height, template="plotly_white", paper_bgcolor="white", plot_bgcolor="white",
        font=dict(family="Noto Sans KR, Arial, sans-serif", color=INK, size=12),
        margin=dict(l=12, r=12, t=50, b=28),
        showlegend=legend,
        legend=dict(orientation="h", y=-0.22, x=0),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor="#E4E8ED")
    fig.update_yaxes(showgrid=False, zeroline=False, linecolor="#E4E8ED")
    return fig


def pct(n, d):
    return float(n) * 100 / float(d) if float(d) > 0 else None


def fmt(v):
    return "데이터 없음" if v is None or pd.isna(v) else f"{v:.1f}%"


def series_rate(table, group_cols, numerator, denominator):
    if table.empty:
        return pd.DataFrame(columns=list(group_cols) + [numerator, denominator, "율"])
    o = table.groupby(list(group_cols), dropna=False)[[numerator, denominator]].sum().reset_index()
    o["율"] = o.apply(lambda r: pct(r[numerator], r[denominator]), axis=1)
    return o


SMALL = 100
if "reset" not in st.session_state:
    st.session_state.reset = 0

def reset_filters():
    st.session_state.reset += 1

n = st.session_state.reset
months = sorted(df["가입월"].dropna().unique().tolist())
channels = sorted(df["가입채널"].dropna().unique().tolist())
platforms = sorted(df["플랫폼"].dropna().unique().tolist())
if not months:
    st.error("분석할 가입월 데이터가 없습니다. CSV와 SQL을 확인해 주세요.")
    st.stop()

st.sidebar.header("🔎 분석 조건")
start, end = st.sidebar.select_slider("가입월 기간", options=months,
    value=(months[0], months[-1]), key=f"기간_{n}")
selected_channels = st.sidebar.multiselect("가입 채널", channels, default=channels, key=f"채널_{n}")
selected_platforms = st.sidebar.multiselect("플랫폼", platforms, default=platforms, key=f"플랫폼_{n}")
with st.sidebar.expander("고급 설정"):
    default_end = months[min(3, len(months)-1)]
    base_start, base_end = st.select_slider("평소 범위 기준 기간", options=months,
        value=(months[0], default_end), key=f"기준_{n}")
    hide_small = st.checkbox(f"가입자 {SMALL}명 미만 집단 제외", value=True, key=f"작은집단_{n}")
st.sidebar.button("필터 초기화", on_click=reset_filters)

# g/rg: 집단만 적용, f/rf: 기간과 집단 적용
g = df[df["가입채널"].isin(selected_channels) & df["플랫폼"].isin(selected_platforms)]
f = g[g["가입월"].between(start, end)]
rg = rd[rd["가입채널"].isin(selected_channels) & rd["플랫폼"].isin(selected_platforms)]
rf = rg[rg["가입월"].between(start, end)]
st.caption(f"🔎 현재 조건 · {start} ~ {end} | 가입 채널 {len(selected_channels)}/{len(channels)}개 | 플랫폼 {len(selected_platforms)}/{len(platforms)}개")
if f.empty:
    st.warning("선택한 조건에 해당하는 데이터가 없습니다. 필터를 다시 선택해 주세요.")
    st.stop()

# 해설은 실시간으로 계산한 기술통계이며 외부 AI API를 호출하지 않습니다.
AI_SOURCE = "📌 원본 CSV 집계 기준 · 현재 선택한 조건 · 자동 생성된 기술통계 (원인 추정은 검증 필요)"

# ------------------------------------------------------------
# 3. 개요
# ------------------------------------------------------------
tab_overview, tab_explore, tab_action = st.tabs(["📊 개요", "🔍 탐색", "✅ 액션"])
with tab_overview:
    st.caption("핵심 KPI → 월별 문제 추이 → 유입 집단 비교 → 재주문 순서로 확인합니다.")
    total = f[["가입자", "식당조회", "장바구니", "결제시작", "첫주문30일"]].sum()
    repeated = rf[["첫주문고객", "재주문30일"]].sum()
    st.markdown("### 핵심 KPI")
    kpi_cards = [
        ("01", "첫 주문 전환율", "가입 후 30일", fmt(pct(total["첫주문30일"], total["가입자"])),
         "첫 주문 고객 ÷ 가입자", "신규 가입자의 구매 전환"),
        ("02", "장바구니 전환율", "식당 조회 → 장바구니", fmt(pct(total["장바구니"], total["식당조회"])),
         "장바구니 이용자 ÷ 식당 조회자", "탐색에서 장바구니로 이어지는 비율"),
        ("03", "30일 재주문율", "첫 주문 후 30일", fmt(pct(repeated["재주문30일"], repeated["첫주문고객"])),
         "30일 내 재주문 고객 ÷ 첫 주문 고객", "첫 주문 이후의 고객 유지"),
    ]
    kpi_columns = st.columns(3, gap="medium")
    for column, (number, title, period_label, value, formula, summary) in zip(kpi_columns, kpi_cards):
        with column:
            with st.container(border=True):
                st.caption(f"KPI {number}  ·  {period_label}")
                st.markdown(f"**{title}**")
                st.metric(label="전환율", value=value, label_visibility="collapsed")
                st.caption(summary)
                with st.popover("ⓘ 계산 기준"):
                    st.write(formula)
                    st.caption("현재 사이드바에서 선택한 가입월·가입 채널·플랫폼 조건이 적용됩니다.")
    st.caption("※ 위 KPI는 가입자 또는 첫 주문 고객의 30일 행동 기준이며, 6주차의 세션 기준 45.7%와 직접 비교하지 않습니다.")

    with st.container(border=True):
        st.markdown("**📏 지금 숫자 읽기**")
        metric_defs = [
            ("30일 첫 주문 전환율", "첫주문30일", "가입자"),
            ("조회 → 장바구니 전환율", "장바구니", "식당조회"),
        ]
        selected_months = [mo for mo in months if start <= mo <= end]
        baseline_months = [mo for mo in months if base_start <= mo <= base_end]
        if len(selected_months) < 3:
            st.info("비교하려면 선택 기간을 3개월 이상 골라 주세요.")
        elif len(baseline_months) < 3:
            st.info("평소 범위를 계산하려면 기준 기간을 3개월 이상 선택해 주세요.")
        else:
            tail = selected_months[-3:]
            curr = f[f["가입월"].isin(tail)]
            baseline = g[g["가입월"].isin(baseline_months)]
            for label, num, den in metric_defs:
                c = pct(curr[num].sum(), curr[den].sum())
                b = series_rate(baseline, ["가입월"], num, den)["율"].dropna()
                if c is None or b.empty:
                    st.write(f"- **{label}:** 비교할 데이터가 부족합니다.")
                    continue
                lo, hi = float(b.min()), float(b.max())
                if c < lo:
                    assessment = f"평소 하한보다 **{lo-c:.1f}%p 낮습니다**"
                elif c > hi:
                    assessment = f"평소 상한보다 **{c-hi:.1f}%p 높습니다**"
                else:
                    assessment = "평소 범위 안입니다"
                st.markdown(f"- {tail[0]} 가입월부터 3개월 **{label} {c:.1f}%**, 평소 범위({lo:.1f}–{hi:.1f}%) 대비 {assessment}.")
            st.caption(f"📏 자동 계산 · 평소 범위 기준: {base_start} ~ {base_end} (월별 분자 합계 ÷ 분모 합계)")
        with st.expander("🤖 AI 해설 · 전체"):
            st.write("전환율 변화는 데이터에서 관찰한 결과입니다. 광고 소재, UI 변경, 쿠폰 정책 등의 효과는 현재 CSV만으로 확인할 수 없어 원인으로 단정하지 않습니다.")
            st.caption(AI_SOURCE)

    with st.container(border=True):
        st.markdown("**① 문제 · 가입월별 30일 첫 주문 전환율**")
        m = series_rate(f, ["가입월"], "첫주문30일", "가입자")
        fig = px.line(m, x="가입월", y="율", markers=True, custom_data=["가입자"],
            labels={"가입월":"가입월", "율":"첫 주문 전환율(%)"})
        fig.update_traces(line=dict(color=NAVY, width=3), marker=dict(size=8),
            hovertemplate="%{x}<br>전환율 %{y:.1f}%<br>가입자 %{customdata[0]:,}명<extra></extra>")
        fig.update_xaxes(type="category")
        st.plotly_chart(style(fig, legend=False), use_container_width=True)

    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            st.markdown("**② 원인 탐색 · 가입 채널별 장바구니 전환율**")
            ch = series_rate(f, ["가입채널"], "장바구니", "식당조회").dropna(subset=["율"])
            ch = ch.sort_values("율")
            fig = go.Figure(go.Bar(x=ch["율"], y=ch["가입채널"], orientation="h",
                marker_color=[NAVY if x == HIGHLIGHT else GRAY for x in ch["가입채널"]],
                text=[f"{x:.1f}%" for x in ch["율"]], textposition="outside",
                customdata=ch[["식당조회"]].to_numpy(),
                hovertemplate="%{y}<br>전환율 %{x:.1f}%<br>식당 조회 %{customdata[0]:,}명<extra></extra>"))
            fig.update_xaxes(visible=False, range=[0, max(100, ch["율"].max()*1.18) if not ch.empty else 100])
            st.plotly_chart(style(fig, height=345, legend=False), use_container_width=True)
    with right:
        with st.container(border=True):
            st.markdown("**③ 제안 근거 · 가입 채널별 30일 재주문율**")
            rc = series_rate(rf, ["가입채널"], "재주문30일", "첫주문고객").dropna(subset=["율"])
            rc = rc.sort_values("율")
            fig = go.Figure(go.Bar(x=rc["율"], y=rc["가입채널"], orientation="h",
                marker_color=[NAVY if x == HIGHLIGHT else GRAY for x in rc["가입채널"]],
                text=[f"{x:.1f}%" for x in rc["율"]], textposition="outside",
                customdata=rc[["첫주문고객"]].to_numpy(),
                hovertemplate="%{y}<br>재주문율 %{x:.1f}%<br>첫 주문 고객 %{customdata[0]:,}명<extra></extra>"))
            fig.update_xaxes(visible=False, range=[0, max(100, rc["율"].max()*1.18) if not rc.empty else 100])
            st.plotly_chart(style(fig, height=345, legend=False), use_container_width=True)

    with st.expander("📥 집계 데이터 내려받기"):
        st.download_button("퍼널 집계 CSV", df.to_csv(index=False).encode("utf-8-sig"), "가입월_채널_플랫폼_퍼널.csv", mime="text/csv")
        st.download_button("재주문 집계 CSV", rd.to_csv(index=False).encode("utf-8-sig"), "가입월_채널_플랫폼_재주문.csv", mime="text/csv")

# ------------------------------------------------------------
# 4. 탐색: 비교 집단, 단계 전환율, 월별 드릴다운
# ------------------------------------------------------------
with tab_explore:
    st.caption("필터 조건 아래 집단을 비교하고, 전환율이 달라진 월을 드릴다운합니다.")
    basis = st.radio("집단 비교 기준", ["가입채널", "플랫폼"], horizontal=True,
        format_func=lambda x: "가입 채널" if x == "가입채널" else "플랫폼")
    dims = ["가입자", "식당조회", "장바구니", "결제시작", "첫주문30일"]
    stage_names = ["가입", "식당 조회", "장바구니", "결제 시작", "첫 주문 30일"]
    group = f.groupby(basis)[dims].sum().reset_index()
    small_names = group.loc[group["가입자"] < SMALL, basis].astype(str).tolist()
    if hide_small:
        group = group[group["가입자"] >= SMALL]
        if small_names:
            st.caption(f"제외한 집단: {', '.join(small_names)} (가입자 {SMALL}명 미만)")
    elif small_names:
        st.caption(f"⚠ 가입자 {SMALL}명 미만인 {', '.join(small_names)}의 지표는 크게 흔들릴 수 있습니다.")
    if group.empty:
        st.warning("표시할 집단이 없습니다. 고급 설정의 작은 집단 제외 옵션을 해제해 주세요.")
    else:
        cumulative = []
        consecutive = []
        for _, r in group.iterrows():
            name = r[basis]
            for i, (stage, num) in enumerate(zip(stage_names, dims)):
                cumulative.append({"집단":name, "단계":stage, "전환율":pct(r[num], r["가입자"]), "단계순서":i})
                if i > 0:
                    consecutive.append({"집단":name, "단계":stage, "전환율":pct(r[num], r[dims[i-1]]),
                                        "직전 단계 사람 수":int(r[dims[i-1]])})
        cum = pd.DataFrame(cumulative)
        cons = pd.DataFrame(consecutive)
        palette = {name: (NAVY if name in (HIGHLIGHT, "Android") else [GRAY, GRAY_LIGHT, NAVY_LIGHT][i%3])
                   for i,name in enumerate(group[basis].tolist())}
        with st.container(border=True):
            st.markdown("**집단 비교 · 누적 전환율과 단계별 전환율**")
            left, right = st.columns(2)
            with left:
                fig = px.funnel(cum, x="전환율", y="단계", color="집단", color_discrete_map=palette,
                    category_orders={"단계":stage_names}, title="가입자 대비 누적 전환율 (%)")
                fig.update_traces(hovertemplate="%{fullData.name}<br>%{y} %{x:.1f}%<extra></extra>")
                st.plotly_chart(style(fig, height=420), use_container_width=True)
            with right:
                fig = px.bar(cons.dropna(subset=["전환율"]), x="단계", y="전환율", color="집단", barmode="group",
                    color_discrete_map=palette, text="전환율", custom_data=["직전 단계 사람 수"],
                    category_orders={"단계":stage_names[1:]}, title="직전 단계 대비 전환율 (%)")
                fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside",
                    hovertemplate="%{fullData.name}<br>%{x}: %{y:.1f}%<br>직전 단계 %{customdata[0]:,}명<extra></extra>")
                fig.update_layout(uniformtext_minsize=9, uniformtext_mode="hide")
                st.plotly_chart(style(fig, height=420), use_container_width=True)
        st.subheader("드릴다운 · 언제부터, 무엇 때문일까")
        a,b = st.columns(2)
        focus = a.selectbox("자세히 볼 집단", group[basis].astype(str).tolist())
        steps = {
            "가입 → 30일 첫 주문": ("첫주문30일", "가입자"),
            "가입 → 식당조회": ("식당조회", "가입자"),
            "식당조회 → 장바구니": ("장바구니", "식당조회"),
            "장바구니 → 결제시작": ("결제시작", "장바구니"),
            "결제시작 → 30일 첫 주문": ("첫주문30일", "결제시작"),
        }
        selection = b.selectbox("전환 단계", list(steps))
        num, den = steps[selection]
        other = "플랫폼" if basis == "가입채널" else "가입채널"
        subset = f[f[basis].astype(str) == focus]
        line = series_rate(subset, ["가입월",other], num, den)
        with st.container(border=True):
            st.markdown(f"**① {focus} · 가입월별 {selection} 전환율 ({other}별)**")
            fig = px.line(line, x="가입월", y="율", color=other, markers=True,
                color_discrete_sequence=[NAVY, GRAY, NAVY_LIGHT, GRAY_LIGHT],
                labels={"가입월":"가입월", "율":"단계 전환율(%)"})
            fig.update_xaxes(type="category")
            st.plotly_chart(style(fig), use_container_width=True)
            st.caption("모든 갈래가 함께 움직였다면, 해당 구분 축만으로 원인을 설명하기는 어렵습니다.")
        month_sizes = f.groupby(["가입월",basis])["가입자"].sum().reset_index()
        target = month_sizes[month_sizes[basis].astype(str)==focus].set_index("가입월")["가입자"]
        tot = f.groupby("가입월")["가입자"].sum()
        composition = pd.DataFrame({"가입월":tot.index, "해당 집단":target.reindex(tot.index,fill_value=0).values})
        composition["그 외"] = tot.values - composition["해당 집단"]
        with st.container(border=True):
            st.markdown("**② 가입월별 구성비 변화 · 선택 집단과 그 외**")
            fig = go.Figure()
            fig.add_bar(name="해당 집단", x=composition["가입월"], y=composition["해당 집단"], marker_color=NAVY)
            fig.add_bar(name="그 외", x=composition["가입월"], y=composition["그 외"], marker_color=GRAY_LIGHT)
            fig.update_layout(barmode="stack", yaxis_title="가입자(명)")
            st.plotly_chart(style(fig), use_container_width=True)
            st.caption("전체 전환율과 집단 비중이 동시에 변하더라도 인과관계가 확정되는 것은 아닙니다.")
        with st.expander(f"🤖 AI 해설 · {focus}"):
            focus_sum = subset[dims].sum()
            st.write(f"{focus} 집단의 선택 기간 가입자 수는 {int(focus_sum['가입자']):,}명, 30일 첫 주문 전환율은 {fmt(pct(focus_sum['첫주문30일'],focus_sum['가입자']))}, 조회 → 장바구니 전환율은 {fmt(pct(focus_sum['장바구니'],focus_sum['식당조회']))}입니다.")
            st.write("월별 변화와 채널/플랫폼의 구성 변화를 우선 확인할 필요가 있습니다. 현재 데이터만으로 광고, UI, 쿠폰 등의 원인을 단정할 수 없습니다.")
            st.caption(AI_SOURCE)

# ------------------------------------------------------------
# 5. 실행 가능한 액션 카드 (기간 마지막 3개월, 집단 선택만 무시)
# ------------------------------------------------------------
with tab_action:
    st.caption("측정 KPI의 현재 값은 선택한 기간의 마지막 3개월 기준으로 계산합니다. 가입 채널·플랫폼 필터는 적용하지 않습니다.")
    recent = [mo for mo in months if start <= mo <= end][-3:]
    f_recent = df[df["가입월"].isin(recent)]
    r_recent = rd[rd["가입월"].isin(recent)]
    focus_name = HIGHLIGHT if HIGHLIGHT in channels else (channels[0] if channels else None)
    a, b = st.columns(2)
    with a:
        with st.container(border=True):
            st.markdown("**액션 카드 ① · organic 장바구니 전환 개선**")
            st.write("**문제:** 식당 조회 이후 장바구니 추가 단계에서 이탈 가능성")
            st.write("**근거:** 6주차 세션 기준 조회 → 장바구니 전환율이 2025-12 53.6%에서 2026-04 43.8%로 하락. 현재 카드 값은 신규 가입자 기준으로 별도 집계")
            st.write("**성격:** 전환 퍼널 개선 가설")
            st.write("**액션:** 식당 상세·메뉴 탐색 및 장바구니 진입 단계의 사용자 행동을 추가 조사하고 개선안 A/B 테스트")
            st.write("**우선순위:** 높음 (원인 검증 선행)")
            seg = f_recent[f_recent["가입채널"] == focus_name] if focus_name else f_recent.iloc[:0]
            k = pct(seg["장바구니"].sum(), seg["식당조회"].sum())
            st.metric("측정 KPI · 조회 → 장바구니", fmt(k))
            st.caption(f"대시보드에서: 개요 ② / 탐색 드릴다운 · {focus_name or '집단 없음'}")
    with b:
        with st.container(border=True):
            st.markdown("**액션 카드 ② · organic 초기 재주문 개선**")
            st.write("**문제:** 첫 주문 후 30일 내 재주문 유지 가능성")
            st.write("**근거:** 6주차 세션 분석에서 organic 첫 주문 고객 30일 재주문율이 76.8% → 73.3%로 하락")
            st.write("**성격:** 초기 리텐션 개선 가설")
            st.write("**액션:** 첫 주문 이후 재방문 구간을 진단하고 재주문 유도 실험을 설계한 뒤 집단 간 KPI 비교")
            st.write("**우선순위:** 중간 (전환 문제와 병행 검증)")
            seg = r_recent[r_recent["가입채널"] == focus_name] if focus_name else r_recent.iloc[:0]
            k = pct(seg["재주문30일"].sum(), seg["첫주문고객"].sum())
            st.metric("측정 KPI · 첫 주문 후 30일 재주문", fmt(k))
            st.caption(f"대시보드에서: 개요 ③ / 탐색 비교 · {focus_name or '집단 없음'}")
    st.info("액션은 관찰 데이터에서 도출한 가설입니다. A/B 테스트나 추가 로그 검증 전에는 원인 및 개선 효과가 확정되지 않습니다.")
