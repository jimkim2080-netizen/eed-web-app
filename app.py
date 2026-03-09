import os
import re
import sqlite3
import datetime as dt
from io import BytesIO
import base64

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

try:
    from gtts import gTTS
except Exception:
    gTTS = None

try:
    from deep_translator import GoogleTranslator
except Exception:
    GoogleTranslator = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "expressions.db")
AUDIO_DIR = os.path.join(BASE_DIR, "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)


def get_conn():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sentences(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_text TEXT,
            target_text TEXT,
            mp3_path TEXT,
            created_at TEXT,
            category TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dictionary(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word TEXT,
            meaning TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def sanitize_filename(text: str) -> str:
    return re.sub(r"[^\w\s-]", "", text).strip().replace(" ", "_")[:40]


def make_mp3_file(text: str, rid: int, lang: str = "en") -> str:
    if not gTTS:
        raise RuntimeError("gTTS가 설치되지 않았습니다. requirements.txt를 확인하세요.")
    if not text.strip():
        raise ValueError("텍스트가 비어 있습니다.")

    fname = f"{sanitize_filename(text)}_{lang}_{rid}.mp3"
    path = os.path.join(AUDIO_DIR, fname)
    gTTS(text=text, lang=lang).save(path)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE sentences SET mp3_path=? WHERE id=?", (path, rid))
    conn.commit()
    conn.close()
    return path


def import_from_excel_file(uploaded_file) -> int:
    df = pd.read_excel(uploaded_file)
    inserted = 0
    conn = get_conn()
    cur = conn.cursor()

    for _, row in df.iterrows():
        eng = str(row.get("English") or "").strip()
        kor = str(row.get("Korean") or "").strip()
        cat = str(row.get("Category") or "").strip()

        if eng.lower() == "nan":
            eng = ""
        if kor.lower() == "nan":
            kor = ""
        if cat.lower() == "nan":
            cat = ""

        if eng:
            cur.execute(
                "INSERT INTO sentences(source_text,target_text,mp3_path,created_at,category) VALUES(?,?,?,?,?)",
                (eng, kor, "", dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), cat),
            )
            inserted += 1

    conn.commit()
    conn.close()
    return inserted


def export_sentences_to_excel_bytes() -> bytes:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, source_text, target_text, mp3_path, created_at, category FROM sentences ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()

    df = pd.DataFrame(rows, columns=["ID", "English", "Korean", "MP3", "Date", "Category"])
    bio = BytesIO()
    df.to_excel(bio, index=False)
    bio.seek(0)
    return bio.getvalue()


def list_sentences(category: str = "전체", limit: int | None = 100) -> pd.DataFrame:
    conn = get_conn()
    cur = conn.cursor()
    sql = "SELECT id, source_text, target_text, mp3_path, created_at, category FROM sentences"
    params = []
    if category and category != "전체":
        sql += " WHERE category=?"
        params.append(category)
    sql += " ORDER BY id DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    cur.execute(sql, tuple(params))
    rows = cur.fetchall()
    conn.close()

    data = []
    for row in rows:
        data.append(
            {
                "ID": row["id"],
                "English": row["source_text"],
                "Korean": row["target_text"],
                "MP3": os.path.basename(row["mp3_path"]) if row["mp3_path"] else "",
                "Date": row["created_at"],
                "Category": row["category"] or "",
            }
        )
    return pd.DataFrame(data)


def get_sentence_by_id(rid: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM sentences WHERE id=?", (rid,))
    row = cur.fetchone()
    conn.close()
    return row


def save_sentence(english: str, korean: str, category: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sentences(source_text,target_text,mp3_path,created_at,category) VALUES(?,?,?,?,?)",
        (english, korean, "", dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), category),
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


def update_sentence(rid: int, english: str, korean: str, category: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE sentences SET source_text=?, target_text=?, category=? WHERE id=?",
        (english, korean, category, rid),
    )
    conn.commit()
    conn.close()


def delete_sentence(rid: int):
    row = get_sentence_by_id(rid)
    if row and row["mp3_path"] and os.path.exists(row["mp3_path"]):
        try:
            os.remove(row["mp3_path"])
        except Exception:
            pass

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM sentences WHERE id=?", (rid,))
    conn.commit()
    conn.close()


def list_categories() -> list[str]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT category FROM sentences WHERE category IS NOT NULL AND TRIM(category) <> '' ORDER BY category")
    rows = cur.fetchall()
    conn.close()
    return ["전체"] + [r[0] for r in rows]


def count_sentences(category: str = "전체") -> int:
    conn = get_conn()
    cur = conn.cursor()
    if category and category != "전체":
        cur.execute("SELECT COUNT(*) FROM sentences WHERE category=?", (category,))
    else:
        cur.execute("SELECT COUNT(*) FROM sentences")
    total = cur.fetchone()[0]
    conn.close()
    return total


def translate_to_korean(text: str) -> str:
    if not GoogleTranslator:
        raise RuntimeError("deep-translator가 설치되지 않았습니다.")
    return GoogleTranslator(source="en", target="ko").translate(text)


def render_repeat_audio_player(audio_path: str, repeat_count: int = 10):
    if not audio_path or not os.path.exists(audio_path):
        st.warning("재생할 MP3 파일이 없습니다.")
        return

    audio_bytes = open(audio_path, "rb").read()
    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    html = f"""
    <div style="padding:0.1rem 0;">
      <audio id="eed_player" controls autoplay style="width:100%;">
        <source src="data:audio/mp3;base64,{audio_b64}" type="audio/mp3">
      </audio>
      <div style="font-size:0.85rem;color:#666;margin-top:0.25rem;">반복 재생: {repeat_count}회</div>
    </div>
    <script>
    const player = document.getElementById("eed_player");
    let playCount = 1;
    const repeatCount = {repeat_count};
    player.onended = function() {{
        if (playCount < repeatCount) {{
            playCount += 1;
            player.currentTime = 0;
            player.play();
        }}
    }};
    </script>
    """
    components.html(html, height=95)


def search_word_meaning(word: str) -> str:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT meaning FROM dictionary WHERE word=? ORDER BY id DESC LIMIT 1", (word,))
    row = cur.fetchone()

    if row:
        meaning = row[0]
    else:
        if not GoogleTranslator:
            meaning = "번역 불가 (deep-translator 미설치)"
        else:
            meaning = GoogleTranslator(source="en", target="ko").translate(word)
            cur.execute("INSERT INTO dictionary(word, meaning) VALUES(?, ?)", (word, meaning))
            conn.commit()

    conn.close()
    return meaning


def add_wordbook(word: str, meaning: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO dictionary(word, meaning) VALUES(?, ?)", (word, meaning))
    conn.commit()
    conn.close()


def list_wordbook() -> pd.DataFrame:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, word, meaning FROM dictionary ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=["ID", "Word", "Meaning"])


def update_wordbook(rid: int, word: str, meaning: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE dictionary SET word=?, meaning=? WHERE id=?", (word, meaning, rid))
    conn.commit()
    conn.close()


def delete_wordbook(rid: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM dictionary WHERE id=?", (rid,))
    conn.commit()
    conn.close()


def queue_sentence_form(sentence_id=None, english="", korean="", category=""):
    st.session_state.pending_sentence_form = {
        "sentence_id": sentence_id,
        "sentence_english": english,
        "sentence_korean": korean,
        "sentence_category": category,
    }


def reset_sentence_form():
    queue_sentence_form(None, "", "", "")


def queue_wordbook_form(wordbook_id=None, word="", meaning=""):
    st.session_state.pending_wordbook_form = {
        "wordbook_id": wordbook_id,
        "wordbook_word": word,
        "wordbook_meaning": meaning,
    }


def reset_wordbook_form():
    queue_wordbook_form(None, "", "")


def prepare_session_state():
    defaults = {
        "sentence_id": None,
        "sentence_english": "",
        "sentence_korean": "",
        "sentence_category": "",
        "wordbook_id": None,
        "wordbook_word": "",
        "wordbook_meaning": "",
        "dict_search_word": "",
        "dict_search_result": "",
        "pending_sentence_form": None,
        "pending_wordbook_form": None,
        "repeat_count": 10,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def apply_pending_updates():
    pending_sentence = st.session_state.get("pending_sentence_form")
    if pending_sentence:
        for key, value in pending_sentence.items():
            st.session_state[key] = value
        st.session_state.pending_sentence_form = None

    pending_wordbook = st.session_state.get("pending_wordbook_form")
    if pending_wordbook:
        for key, value in pending_wordbook.items():
            st.session_state[key] = value
        st.session_state.pending_wordbook_form = None


def apply_compact_css():
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 0.7rem !important;
            padding-bottom: 0.6rem !important;
            max-width: 1200px;
        }
        h1, h2, h3 { margin-top: 0.1rem !important; margin-bottom: 0.45rem !important; }
        div[data-testid="stVerticalBlock"] > div:has(> div[data-testid="stButton"]) {
            gap: 0.35rem;
        }
        div[data-testid="stDataFrame"] {
            border-radius: 10px;
            overflow: hidden;
        }
        @media (max-width: 768px) {
            .block-container {
                padding-left: 0.55rem !important;
                padding-right: 0.55rem !important;
                padding-top: 0.45rem !important;
            }
            button[kind="primary"], button[kind="secondary"] {
                min-height: 2.75rem !important;
                font-size: 1rem !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sentence_editor():
    st.markdown("#### 입력 / 수정")
    english = st.text_area("영어", key="sentence_english", height=100)
    korean = st.text_area("한글", key="sentence_korean", height=100)
    st.text_input("카테고리", key="sentence_category")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button("저장", use_container_width=True, key="btn_sentence_save"):
            if not english.strip():
                st.warning("영문 입력이 필요합니다.")
            else:
                rid = save_sentence(english.strip(), korean.strip(), st.session_state.sentence_category.strip())
                st.session_state.sentence_id = rid
                st.success(f"저장 완료: ID {rid}")
                st.rerun()
    with c2:
        if st.button("수정", use_container_width=True, key="btn_sentence_update"):
            if not st.session_state.sentence_id:
                st.warning("먼저 목록에서 문장을 선택하세요.")
            else:
                update_sentence(
                    st.session_state.sentence_id,
                    english.strip(),
                    korean.strip(),
                    st.session_state.sentence_category.strip(),
                )
                st.success("수정되었습니다.")
                st.rerun()
    with c3:
        if st.button("삭제", use_container_width=True, key="btn_sentence_delete"):
            if not st.session_state.sentence_id:
                st.warning("먼저 목록에서 문장을 선택하세요.")
            else:
                delete_sentence(st.session_state.sentence_id)
                reset_sentence_form()
                st.success("삭제되었습니다.")
                st.rerun()
    with c4:
        if st.button("초기화", use_container_width=True, key="btn_sentence_reset"):
            reset_sentence_form()
            st.rerun()

    c5, c6 = st.columns(2)
    with c5:
        if st.button("번역 EN→KO", use_container_width=True, key="btn_translate_sentence"):
            if not english.strip():
                st.warning("영어 문장을 먼저 입력하세요.")
            else:
                try:
                    translated = translate_to_korean(english.strip())
                    queue_sentence_form(
                        sentence_id=st.session_state.sentence_id,
                        english=english.strip(),
                        korean=translated,
                        category=st.session_state.sentence_category.strip(),
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"번역 오류: {e}")
    with c6:
        if st.button("MP3 생성", use_container_width=True, key="btn_make_mp3"):
            if not st.session_state.sentence_id:
                st.warning("먼저 저장하거나 목록에서 항목을 선택하세요.")
            elif not english.strip():
                st.warning("영문 문장이 비어 있습니다.")
            else:
                try:
                    path = make_mp3_file(english.strip(), st.session_state.sentence_id, "en")
                    st.success(f"MP3 생성 완료: {os.path.basename(path)}")
                    st.audio(path)
                except Exception as e:
                    st.error(f"MP3 오류: {e}")


def render_sentence_list_and_player():
    top1, top2, top3 = st.columns([1.2, 0.9, 0.9])
    with top1:
        selected_category = st.selectbox("카테고리", list_categories(), index=0, key="select_sentence_category")
    with top2:
        row_limit = st.selectbox("표시 개수", [50, 100, 200, 500], index=1, key="select_sentence_limit")
    with top3:
        repeat_count = st.selectbox("재생 횟수", [1, 3, 5, 10], index=3, key="repeat_count")

    total_count = count_sentences(selected_category)
    df = list_sentences(selected_category, limit=row_limit)
    st.caption(f"{selected_category} · 총 {total_count:,}개 · 화면 {min(total_count, row_limit):,}개")

    if df.empty:
        st.warning("저장된 문장이 없습니다.")
        return

    st.dataframe(df, use_container_width=True, hide_index=True, height=280)
    options = [f"{row.ID} | {row.English[:60]}" for row in df.itertuples(index=False)]
    selected_label = st.selectbox("문장 선택", options, key="select_sentence_row")
    selected_id = int(selected_label.split("|")[0].strip())
    row = get_sentence_by_id(selected_id)

    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("폼 불러오기", use_container_width=True, key="btn_load_sentence_form"):
            queue_sentence_form(
                sentence_id=row["id"],
                english=row["source_text"] or "",
                korean=row["target_text"] or "",
                category=row["category"] or "",
            )
            st.rerun()
    with b2:
        if st.button("1회 재생", use_container_width=True, key="btn_play_once"):
            if row and row["mp3_path"] and os.path.exists(row["mp3_path"]):
                render_repeat_audio_player(row["mp3_path"], 1)
            else:
                st.warning("해당 문장의 MP3가 없습니다.")
    with b3:
        if st.button(f"반복 재생 ({repeat_count}회)", use_container_width=True, key="btn_play_repeat"):
            if row and row["mp3_path"] and os.path.exists(row["mp3_path"]):
                render_repeat_audio_player(row["mp3_path"], int(repeat_count))
            else:
                st.warning("해당 문장의 MP3가 없습니다.")



def render_excel_tools():
    with st.expander("Excel 가져오기 / 내보내기", expanded=False):
        uploaded = st.file_uploader("Excel 가져오기 (.xlsx)", type=["xlsx"], key="excel_uploader")
        if uploaded is not None:
            if st.button("업로드한 Excel DB에 반영", use_container_width=True, key="btn_import_excel"):
                try:
                    inserted = import_from_excel_file(uploaded)
                    st.success(f"{inserted}건 불러왔습니다.")
                    st.rerun()
                except Exception as e:
                    st.error(f"엑셀 가져오기 오류: {e}")

        excel_bytes = export_sentences_to_excel_bytes()
        st.download_button(
            "엑셀로 내보내기",
            data=excel_bytes,
            file_name="eed_sentences_export.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


def render_word_search():
    st.markdown("#### 단어 검색")
    st.text_input("영어 단어 입력", key="dict_search_word")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("검색", use_container_width=True, key="btn_dict_search"):
            word = st.session_state.dict_search_word.strip()
            if not word:
                st.warning("단어를 입력하세요.")
            else:
                try:
                    meaning = search_word_meaning(word)
                    st.session_state.dict_search_result = meaning
                    st.rerun()
                except Exception as e:
                    st.error(f"검색 오류: {e}")
    with c2:
        if st.button("단어장 저장", use_container_width=True, key="btn_dict_save_wordbook"):
            word = st.session_state.dict_search_word.strip()
            meaning = st.session_state.dict_search_result.strip()
            if not word or not meaning:
                st.warning("먼저 검색 결과를 만드세요.")
            else:
                add_wordbook(word, meaning)
                st.success("단어장에 저장했습니다.")

    st.text_area("검색 결과", value=st.session_state.dict_search_result, height=180, disabled=True, key="dict_search_result_box")


def render_wordbook():
    sub1, sub2 = st.tabs(["단어 입력", "단어 목록"])

    with sub1:
        st.text_input("Word", key="wordbook_word")
        st.text_area("Meaning", key="wordbook_meaning", height=110)
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            if st.button("추가", use_container_width=True, key="btn_wordbook_add"):
                w = st.session_state.wordbook_word.strip()
                m = st.session_state.wordbook_meaning.strip()
                if not w or not m:
                    st.warning("Word/Meaning을 입력하세요.")
                else:
                    add_wordbook(w, m)
                    reset_wordbook_form()
                    st.success("추가되었습니다.")
                    st.rerun()
        with c2:
            if st.button("수정", use_container_width=True, key="btn_wordbook_update"):
                if not st.session_state.wordbook_id:
                    st.warning("먼저 단어 목록에서 선택하세요.")
                else:
                    w = st.session_state.wordbook_word.strip()
                    m = st.session_state.wordbook_meaning.strip()
                    if not w or not m:
                        st.warning("Word/Meaning을 입력하세요.")
                    else:
                        update_wordbook(st.session_state.wordbook_id, w, m)
                        st.success("수정되었습니다.")
                        st.rerun()
        with c3:
            if st.button("삭제", use_container_width=True, key="btn_wordbook_delete"):
                if not st.session_state.wordbook_id:
                    st.warning("먼저 단어 목록에서 선택하세요.")
                else:
                    delete_wordbook(st.session_state.wordbook_id)
                    reset_wordbook_form()
                    st.success("삭제되었습니다.")
                    st.rerun()
        with c4:
            if st.button("초기화", use_container_width=True, key="btn_wordbook_reset"):
                reset_wordbook_form()
                st.rerun()

    with sub2:
        wb_df = list_wordbook()
        if wb_df.empty:
            st.warning("단어장에 저장된 항목이 없습니다.")
        else:
            st.dataframe(wb_df, use_container_width=True, hide_index=True, height=320)
            options = [f"{row.ID} | {row.Word}" for row in wb_df.itertuples(index=False)]
            selected_word = st.selectbox("단어 선택", options, key="select_wordbook_row")
            selected_id = int(selected_word.split("|")[0].strip())
            selected_row = wb_df[wb_df["ID"] == selected_id].iloc[0]
            if st.button("선택 단어 폼 불러오기", use_container_width=True, key="btn_load_wordbook_form"):
                queue_wordbook_form(
                    wordbook_id=int(selected_row["ID"]),
                    word=selected_row["Word"],
                    meaning=selected_row["Meaning"],
                )
                st.rerun()


def main():
    st.set_page_config(page_title="EED Web v5", page_icon="📘", layout="wide")
    init_db()
    prepare_session_state()
    apply_pending_updates()
    apply_compact_css()

    st.title("📘 EED Web v5")
    st.caption("선택 문장 영역을 제거하고, 안드로이드에서도 보기 쉽게 정리한 버전")

    tab1, tab2, tab3 = st.tabs(["표현 사전", "단어 검색", "단어장"])

    with tab1:
        sub1, sub2, sub3 = st.tabs(["입력/수정", "목록/재생", "Excel"])
        with sub1:
            render_sentence_editor()
        with sub2:
            render_sentence_list_and_player()
        with sub3:
            render_excel_tools()

    with tab2:
        render_word_search()

    with tab3:
        render_wordbook()

    st.caption("배포 후 안드로이드 Chrome에서 URL로 접속하고, 홈 화면에 추가하면 앱처럼 사용할 수 있습니다.")


if __name__ == "__main__":
    main()
