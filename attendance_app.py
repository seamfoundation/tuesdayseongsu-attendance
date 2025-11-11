import streamlit as st
import gspread
import time
from google.oauth2.service_account import Credentials
from datetime import datetime
from gspread.exceptions import APIError

# ────────────────────────────────
# Google Sheets 인증
# ────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
service_account_info = st.secrets["google"]
creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
client = gspread.authorize(creds)

# 시트 연결
sheet = client.open_by_key("1S_heqlCi0j33RgcSWBvVAPKhApSh3yGWF6x7yOuCU1g")
ws = sheet.sheet1
church_ws = sheet.worksheet("church_list")
log_ws = sheet.worksheet("attendance_log")

# ────────────────────────────────
# 공통 유틸 함수
# ────────────────────────────────
def safe_int(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def safe_write(func, *args, retries=3, delay=1, **kwargs):
    """Google API 요청 안전 실행 (자동 재시도 + 쿨다운)"""
    for attempt in range(retries):
        try:
            result = func(*args, **kwargs)
            time.sleep(delay)  # ✅ 쿨다운 (요청 간격 최소 1초)
            return result
        except APIError as e:
            if attempt < retries - 1:
                st.warning(f"서버 혼잡으로 재시도 중... ({attempt+1}/{retries})")
                time.sleep(delay * 2)
            else:
                st.error("⚠️ 구글시트 요청이 너무 많습니다. 잠시 후 다시 시도해주세요.")
                raise e


# ────────────────────────────────
# 출석 로그 기록
# ────────────────────────────────
def log_attendance(name, church, church_id, is_new, count):
    today = datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe_write(log_ws.append_row, [today, name, church, "신규" if is_new else "기존", count, church_id, now_time])


# ────────────────────────────────
# 교회 ID 초기화
# ────────────────────────────────
def initialize_church_ids():
    data = church_ws.get_all_values()
    if not data:
        return
    headers = data[0]
    if "교회 id" not in headers:
        st.error("church_list 시트에 '교회 id' 열이 필요합니다.")
        return

    id_col = headers.index("교회 id") + 1
    for i, row in enumerate(data[1:], start=2):
        if len(row) < id_col or not row[id_col - 1].strip():
            safe_write(church_ws.update_cell, i, id_col, f"CH{i-1:03d}")


# ────────────────────────────────
# 교회 등록 or ID 반환
# ────────────────────────────────
def ensure_church_exists(church_name, region="미입력"):
    data = church_ws.get_all_records()
    today = datetime.now().strftime("%Y-%m-%d")

    if church_name == "미소속":
        return "CH000"

    for idx, row in enumerate(data, start=2):
        if row.get("교회명") == church_name:
            count = safe_int(row.get("누적 예배자")) + 1
            safe_write(church_ws.update, f"E{idx}", [[count]])
            return row.get("교회 id")

    new_id = f"CH{len(data) + 1:03d}"
    safe_write(church_ws.append_row, [new_id, church_name, region, today, 1])
    return new_id


# ────────────────────────────────
# 기존 예배자 출석
# ────────────────────────────────
def handle_attendance(row, row_idx):
    today = datetime.now().strftime("%Y-%m-%d")
    last_date = str(row.get("최근출석일", ""))

    if last_date == today:
        st.info(f"{row['이름']} 님은 오늘 이미 출석하셨습니다 🙏")
    else:
        count = safe_int(row.get("출석횟수")) + 1
        safe_write(ws.batch_update, [{
            'range': f"C{row_idx}:D{row_idx}",
            'values': [[count, today]]
        }])
        st.success(f"{row['이름']} 님, 오늘로 {count}번째 출석입니다 🙌")
        log_attendance(row["이름"], row["소속교회"], row.get("교회id", ""), False, count)

    st.session_state.show_registration = False
    st.session_state.show_select_church = False


# ────────────────────────────────
# 초기화
# ────────────────────────────────
if "initialized" not in st.session_state:
    initialize_church_ids()
    st.session_state.initialized = True

st.title("✝️ 서울숲 화요성수 예배 출석 체크")

name = st.text_input("이름을 입력하세요")

# ────────────────────────────────
# [1] 이름 입력 처리
# ────────────────────────────────
if st.button("확인"):
    if not name:
        st.warning("이름을 입력해주세요.")
    else:
        data = ws.get_all_records()
        matches = [row for row in data if row.get("이름") == name]

        if len(matches) == 0:
            st.warning(f"{name} 님의 정보가 없습니다. 아래에서 신규 등록을 진행해주세요 🙏")
            st.session_state.name = name
            st.session_state.show_registration = True
        else:
            st.session_state.name = name
            st.session_state.matches = matches
            st.session_state.show_select_church = True


# ────────────────────────────────
# [2] 동명이인 / 단일 매칭 확인창
# ────────────────────────────────
if st.session_state.get("show_select_church", False):
    matches = st.session_state.matches
    name = st.session_state.name

    st.subheader(f"🔍 {name} 님의 정보가 아래에 있습니다.")
    options = [row["소속교회"] for row in matches]
    selected_church = st.selectbox("소속 교회를 선택하세요", options)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ 네, 저 맞아요 (출석 체크)"):
            match = next(row for row in matches if row["소속교회"] == selected_church)
            data = ws.get_all_records()
            row_idx = data.index(match) + 2
            handle_attendance(match, row_idx)
            st.session_state.show_select_church = False

    with col2:
        if st.button("🆕 처음 오셨나요? (신규 등록)"):
            st.session_state.show_select_church = False
            st.session_state.show_registration = True
            st.rerun()


# ────────────────────────────────
# [3] 신규 등록 폼
# ────────────────────────────────
if st.session_state.get("show_registration", False):
    st.markdown("---")
    st.subheader(f"🌿 {st.session_state.name} 님 신규 등록")

    st.markdown("#### 📜 개인정보 이용 동의")
    st.info(
        "서울숲 화요성수 모임은 재단법인 심센터를 통해 운영되며, "
        "개인정보는 출석 관리 및 안내/홍보 목적 외에는 사용되지 않습니다.\n\n"
        "개인정보는 재단법인 심센터의 행사 및 모임 안내에 사용됩니다."
    )
    agree = st.checkbox("위의 개인정보 이용에 동의합니다.")

    if agree:
        church_data = church_ws.get_all_records()
        church_data_sorted = sorted(church_data, key=lambda x: x["교회명"])
        church_options = [f"{c['교회명']} ({c['지역']})" for c in church_data_sorted]

        st.markdown("#### 🕍 소속 교회 선택")
        selected = st.selectbox(
            "교회를 선택하세요 (목록에 없으면 새로 등록)",
            ["미소속"] + ["-- 교회 선택 --"] + church_options + ["➕ 새 교회 등록"]
        )

        if selected == "미소속":
            new_church_name = "미소속"
            new_region = "미입력"
        elif selected == "➕ 새 교회 등록":
            new_church_name = st.text_input("새 교회 이름을 입력하세요").replace(" ", "")
            new_region = st.text_input("교회 지역명 (예: 서울 성동구)")
        elif selected != "-- 교회 선택 --":
            new_church_name = selected.split(" (")[0]
            new_region = next(
                (c["지역"] for c in church_data_sorted if c["교회명"] == new_church_name),
                "미입력"
            )
        else:
            new_church_name, new_region = None, None

        phone = st.text_input("📞 연락처 (-없이 입력) ")
        email = st.text_input("📧 이메일 ")

        if st.button("신규 등록하기"):
            if not new_church_name:
                st.error("교회를 선택하거나 새로 등록해주세요.")
            else:
                today = datetime.now().strftime("%Y-%m-%d")
                church_id = ensure_church_exists(new_church_name, new_region)
                safe_write(ws.append_row, [
                    st.session_state.name, new_church_name, 1, today, today,
                    phone, email, church_id
                ])
                log_attendance(st.session_state.name, new_church_name, church_id, True, 1)
                st.success(f"{st.session_state.name} 님, 첫 출석이 완료되었습니다 🌿 환영합니다!")
                st.session_state.show_registration = False
    else:
        st.warning("개인정보 이용 동의가 필요합니다.")

