import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from geopy.distance import geodesic
from geopy.geocoders import Nominatim
import plotly.express as px
import time
from PIL import Image, ImageOps
import io
import datetime

# --- ライブラリ ---
import gspread
from google.oauth2.service_account import Credentials
import cloudinary
import cloudinary.uploader

# --- 1. アプリの設定 ---
st.set_page_config(page_title="ライブ参戦記録 & 推し活マップ", layout="wide")
st.title("🎸 ライブ参戦記録 & 推し活マップ (Fixed Edit)")

# デフォルトの拠点（東京駅）
DEFAULT_HOME_COORDS = (35.6812, 139.7671)

# --- フォームリセット処理 ---
if "should_clear_form" not in st.session_state:
    st.session_state["should_clear_form"] = False

if "uploader_key" not in st.session_state:
    st.session_state["uploader_key"] = "1"

if st.session_state["should_clear_form"]:
    st.session_state["input_date"] = datetime.date.today()
    st.session_state["input_live"] = ""
    st.session_state["input_artist"] = ""
    st.session_state["input_venue"] = ""
    st.session_state["input_comment"] = ""
    st.session_state["uploader_key"] = str(time.time())
    st.session_state["should_clear_form"] = False

# --- 2. 認証 & 設定 ---
@st.cache_resource
def init_services():
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
        gc = gspread.authorize(creds)
    except Exception as e:
        st.error(f"❌ Google認証エラー: {e}")
        return None, None

    try:
        c_config = st.secrets["cloudinary"]
        cloudinary.config(
            cloud_name = c_config["cloud_name"],
            api_key = c_config["api_key"],
            api_secret = c_config["api_secret"],
            secure = True
        )
    except Exception:
        pass
    return gc, creds

gc, creds = init_services()
if gc is None: st.stop()

try:
    spreadsheet_id = st.secrets["app_config"]["spreadsheet_id"]
    sh = gc.open_by_key(spreadsheet_id)
    worksheet = sh.sheet1
except Exception:
    st.stop()

# --- 3. ヘルパー関数 & 辞書設定 ---
geolocator = Nominatim(user_agent="my_live_app_mvp_v33")

# 名寄せ辞書
VENUE_NAME_MAP = {
    "Kアリーナ": "Kアリーナ横浜",
    "kアリーナ": "Kアリーナ横浜",
    "Ｋアリーナ": "Kアリーナ横浜",
    "横浜アリーナ": "横浜アリーナ",
    "横アリ": "横浜アリーナ",
    "ヨコアリ": "横浜アリーナ",
    "愛知スカイエキスポ": "Aichi Sky Expo",
    "スカイエキスポ": "Aichi Sky Expo",
    "愛知県国際展示場": "Aichi Sky Expo",
    "AICHI SKY EXPO": "Aichi Sky Expo",
    "東京ドーム": "東京ドーム",
    "京王アリーナ": "京王アリーナTOKYO"
    "京王アリーナ東京": "京王アリーナTOKYO" 
}

VENUE_OVERRIDES = {
    "Aichi Sky Expo": [34.8613, 136.8123],
    "恵比寿ザ・ガーデンホール": [35.6421, 139.7132],
    "横浜アリーナ": [35.5175, 139.6172],
    "Kアリーナ横浜": [35.4636, 139.6310],
    "日本武道館": [35.6933, 139.7498],
}

def normalize_venue_name(name):
    if not name: return ""
    return VENUE_NAME_MAP.get(name, name)

@st.cache_data
def get_location_cached(place_name):
    if not place_name: return None
    normalized_name = normalize_venue_name(place_name)
    if normalized_name in VENUE_OVERRIDES:
        return VENUE_OVERRIDES[normalized_name]
    try:
        time.sleep(1)
        location = geolocator.geocode(normalized_name)
        if location: return location.latitude, location.longitude
    except: return None
    return None

def upload_photo_to_cloudinary(uploaded_file):
    if uploaded_file is None: return None
    try:
        image_bytes = uploaded_file.getvalue()
        response = cloudinary.uploader.upload(image_bytes, folder="live_app_photos", resource_type="image")
        return response['secure_url']
    except Exception as e: return f"ERROR: {e}"

def get_fiscal_year(date_obj):
    if pd.isnull(date_obj): return "不明"
    try:
        if isinstance(date_obj, str): date_obj = pd.to_datetime(date_obj)
        year = date_obj.year
        month = date_obj.month
        if month < 1: return year - 1
        return year
    except: return "不明"

# --- 4. データ操作 ---
def load_data(current_user_id):
    try:
        data = worksheet.get_all_records()
        for i, row in enumerate(data): row['_row_index'] = i + 2
        df = pd.DataFrame(data)
        
        if df.empty: return pd.DataFrame()
        df.columns = df.columns.str.strip()
        
        required_cols = ["日付", "ライブ名", "アーティスト", "会場名", "感想", "写真", "lat", "lon", "ユーザーID"]
        for col in required_cols:
            if col not in df.columns: df[col] = None
        
        if "lat" in df.columns:
            df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
            df["lon"] = pd.to_numeric(df["lon"], errors="coerce")

        if current_user_id:
            df["ユーザーID"] = df["ユーザーID"].astype(str)
            df = df[df["ユーザーID"] == str(current_user_id)]
        else:
            return pd.DataFrame()

        if "会場名" in df.columns:
            df["会場名"] = df["会場名"].apply(normalize_venue_name)

        if "日付" in df.columns and not df.empty:
            df["日付"] = pd.to_datetime(df["日付"])
            df = df.sort_values("日付", ascending=False)
            df["年度"] = df["日付"].apply(get_fiscal_year)
            
        return df
    except: return pd.DataFrame()

def add_record(record_dict):
    record_dict["会場名"] = normalize_venue_name(record_dict["会場名"])
    row = [str(record_dict["日付"]), record_dict["ライブ名"], record_dict["アーティスト"], record_dict["会場名"], record_dict["感想"], record_dict["写真"], record_dict["lat"], record_dict["lon"], record_dict["ユーザーID"]]
    worksheet.append_row(row)
    st.cache_data.clear()

def delete_records(row_indices):
    for idx in sorted(row_indices, reverse=True): worksheet.delete_rows(idx)
    st.cache_data.clear()

def update_record(row_index, record_dict):
    record_dict["会場名"] = normalize_venue_name(record_dict["会場名"])
    cell_range = f"A{row_index}:I{row_index}"
    values = [[str(record_dict["日付"]), record_dict["ライブ名"], record_dict["アーティスト"], record_dict["会場名"], record_dict["感想"], record_dict["写真"], record_dict["lat"], record_dict["lon"], record_dict["ユーザーID"]]]
    worksheet.update(range_name=cell_range, values=values)
    st.cache_data.clear()

# --- 5. アプリ本体 ---

# === サイドバー ===
st.sidebar.title("👤 設定 & フィルター")

if "user_id" not in st.session_state:
    st.session_state["user_id"] = ""

input_user_id = st.sidebar.text_input("ユーザーID", value=st.session_state["user_id"], placeholder="例: taro123")
if input_user_id: st.session_state["user_id"] = input_user_id.strip()
current_user = st.session_state["user_id"]

if not current_user:
    st.warning("ユーザー名を入力してください")
    st.stop()

if 'data' not in st.session_state:
    st.session_state.data = load_data(current_user)
else:
    st.session_state.data = load_data(current_user)

df_all = st.session_state.data

# 期間フィルター
if not df_all.empty:
    years = sorted(df_all["年度"].unique().tolist(), reverse=True)
    options = ["全期間"] + [f"{y}年度" for y in years if y != "不明"]
    selected_period = st.sidebar.radio("📅 表示期間", options)
    
    if selected_period == "全期間":
        df_display = df_all
    else:
        target_year = int(selected_period.replace("年度", ""))
        df_display = df_all[df_all["年度"] == target_year]
else:
    df_display = df_all
    selected_period = "全期間"

st.sidebar.divider()

with st.sidebar.expander("🏠 拠点の入力", expanded=True):
    user_home_name = st.text_input("拠点（駅名など）", placeholder="例：新大阪駅")
    home_coords = DEFAULT_HOME_COORDS
    home_display_name = "東京駅"
    if user_home_name:
        found_coords = get_location_cached(user_home_name)
        if found_coords:
            home_coords = found_coords
            home_display_name = user_home_name

st.sidebar.header("📝 新規記録")
with st.sidebar.form("entry_form"):
    date = st.date_input("日付", key="input_date", value=datetime.date.today())
    live_name = st.text_input("ライブ名")
    artist = st.text_input("アーティスト")
    venue = st.text_input("会場名")
    comment = st.text_area("感想")
    photo = st.file_uploader("写真", type=["jpg", "png"], key=st.session_state["uploader_key"])
    
    if st.form_submit_button("記録 (Cloud保存)"):
        if not venue or not artist:
            st.error("必須項目が足りません")
        else:
            with st.spinner("保存中..."):
                coords = get_location_cached(venue)
                if coords:
                    photo_url = "None"
                    if photo:
                        res = upload_photo_to_cloudinary(photo)
                        if res and not str(res).startswith("ERROR"): photo_url = res
                    
                    add_record({
                        "日付": date, "ライブ名": live_name, "アーティスト": artist,
                        "会場名": venue, "感想": comment, "写真": photo_url,
                        "lat": coords[0], "lon": coords[1],
                        "ユーザーID": current_user
                    })
                    st.success("保存しました！")
                    st.session_state["should_clear_form"] = True
                    st.rerun()
                else:
                    st.error("会場が見つかりません")

# === メイン画面 ===
if df_display.empty:
    if df_all.empty:
        st.info("データがありません。サイドバーから記録を追加してください。")
    else:
        st.warning(f"「{selected_period}」のデータはありません。")
else:
    st.markdown(f"### 📊 {selected_period} の推し活状況")

    tab1, tab2 = st.tabs(["🗺️ マップ", "📝 リスト & 分析"])

    with tab1:
        total_distance_km = 0
        for index, row in df_display.iterrows():
            if pd.notnull(row['lat']) and pd.notnull(row['lon']):
                venue_loc = (row['lat'], row['lon'])
                dist = geodesic(home_coords, venue_loc).km * 2
                total_distance_km += dist
        
        c1, c2 = st.columns(2)
        c1.metric("参戦数", f"{len(df_display)} 回")
        c2.metric("総移動距離", f"{int(total_distance_km):,} km")
        
        center_lat = df_display['lat'].mean()
        center_lon = df_display['lon'].mean()
        m = folium.Map(location=[center_lat, center_lon], zoom_start=5)
        
        folium.Marker(home_coords, icon=folium.Icon(color="blue", icon="home"), tooltip=home_display_name).add_to(m)

        grouped = df_display.groupby('会場名')
        for venue_name, group in grouped:
            lat = group.iloc[0]['lat']
            lon = group.iloc[0]['lon']
            count = len(group)
            
            html = f"""
            <div style="font-family:sans-serif; width:300px; max-height:300px; overflow-y:auto;">
                <h4 style="color:#E63946; margin-bottom:5px; position:sticky; top:0; background:white; z-index:1;">
                    <b>{venue_name}</b>
                </h4>
                <p><b>🏆 {selected_period}の参戦: {count}回</b></p>
                <hr>
            """
            
            group = group.sort_values('日付', ascending=False)
            for _, row in group.iterrows():
                img_tag = ""
                photo_val = str(row.get("写真", ""))
                if photo_val and photo_val != "None" and photo_val.startswith("http"):
                    img_tag = f'<img src="{photo_val}" style="width:100%; border-radius:5px; margin-bottom:5px;">'
                
                date_str = row['日付'].strftime('%Y-%m-%d')
                html += f"""
                <div style="margin-bottom:15px; background:#f9f9f9; padding:10px; border-radius:5px;">
                    📅 {date_str}<br>
                    🎤 <b>{row['アーティスト']}</b><br>
                    🎵 {row['ライブ名']}<br>
                    {img_tag}
                    💬 {row['感想']}<br>
                </div>
                """
            html += "</div>"
            
            folium.Marker(
                [lat, lon],
                popup=folium.Popup(html, max_width=320),
                tooltip=f"{venue_name} ({count}回)",
                icon=folium.Icon(color="red", icon="music")
            ).add_to(m)
        
        st_folium(m, width="100%", height=400, returned_objects=[])

    with tab2:
        if "アーティスト" in df_display.columns:
            st.write("#### 🎨 アーティスト比率")
            counts = df_display['アーティスト'].value_counts().reset_index()
            counts.columns = ['アーティスト', '回数']
            col_graph, col_table = st.columns([0.6, 0.4])
            with col_graph:
                fig = px.pie(counts, values='回数', names='アーティスト')
                st.plotly_chart(fig, use_container_width=True)
            with col_table:
                st.dataframe(counts, hide_index=True)

        st.divider()
        st.write("#### 📜 記録一覧")
        
        display_cols = ["日付", "ライブ名", "アーティスト", "会場名", "感想"]
        event = st.dataframe(
            df_display[display_cols],
            on_select="rerun",
            selection_mode="multi-row",
            hide_index=True,
            use_container_width=True
        )

        selected_rows = event.selection.rows
        
        if selected_rows:
            selected_df = df_display.iloc[selected_rows]
            st.write("---")
            
            # 🗑️ 削除ボタン
            if st.button(f"🗑️ 選択した {len(selected_rows)} 件を削除"):
                target_indices = selected_df['_row_index'].tolist()
                delete_records(target_indices)
                st.success("削除しました")
                st.rerun()

            # ✏️ 編集モード (ここを復活させました！)
            if len(selected_rows) == 1:
                st.markdown("#### ✏️ 編集モード")
                target_row = selected_df.iloc[0]
                target_sheet_index = target_row['_row_index']
                
                with st.form("edit_form"):
                    try:
                        default_date = pd.to_datetime(target_row["日付"]).date()
                    except:
                        default_date = datetime.date.today()

                    e_date = st.date_input("日付", value=default_date)
                    e_live = st.text_input("ライブ名", value=target_row["ライブ名"])
                    e_artist = st.text_input("アーティスト", value=target_row["アーティスト"])
                    e_venue = st.text_input("会場名", value=target_row["会場名"])
                    e_comment = st.text_area("感想", value=target_row["感想"])
                    st.caption("写真を変更したい場合のみアップロードしてください")
                    e_photo = st.file_uploader("写真の変更", type=["jpg", "png", "jpeg"])
                    
                    if st.form_submit_button("変更を保存"):
                        with st.spinner("更新中..."):
                            new_lat, new_lon = target_row["lat"], target_row["lon"]
                            # 会場名が変わったら座標再取得
                            # (名寄せは update_record の中で行われます)
                            if e_venue != target_row["会場名"]:
                                coords = get_location_cached(e_venue)
                                if coords:
                                    new_lat, new_lon = coords
                            
                            new_photo_url = target_row["写真"]
                            if e_photo:
                                res = upload_photo_to_cloudinary(e_photo)
                                if res and not str(res).startswith("ERROR"):
                                    new_photo_url = res
                            
                            updated_record = {
                                "日付": e_date,
                                "ライブ名": e_live,
                                "アーティスト": e_artist,
                                "会場名": e_venue,
                                "感想": e_comment,
                                "写真": new_photo_url,
                                "lat": new_lat,
                                "lon": new_lon,
                                "ユーザーID": current_user
                            }
                            
                            update_record(target_sheet_index, updated_record)
                            st.success("更新しました！")
                            st.session_state.data = load_data(current_user)
                            st.rerun()