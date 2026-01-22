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
st.title("🎸 ライブ参戦記録 & 推し活マップ")

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
    # Google Sheets認証
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
        ]
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"], scopes=scopes
        )
        gc = gspread.authorize(creds)
    except Exception as e:
        st.error(f"❌ Google認証エラー: {e}")
        return None, None

    # Cloudinary設定
    try:
        c_config = st.secrets["cloudinary"]
        cloudinary.config(
            cloud_name = c_config["cloud_name"],
            api_key = c_config["api_key"],
            api_secret = c_config["api_secret"],
            secure = True
        )
    except Exception as e:
        pass # エラーは無視（機能しないだけ）
    
    return gc, creds

gc, creds = init_services()

if gc is None:
    st.stop()

try:
    spreadsheet_id = st.secrets["app_config"]["spreadsheet_id"]
    sh = gc.open_by_key(spreadsheet_id)
    worksheet = sh.sheet1
except Exception as e:
    st.error(f"⚠️ スプレッドシート接続エラー: {e}")
    st.stop()

# --- 3. ヘルパー関数たち ---
geolocator = Nominatim(user_agent="my_live_app_mvp_v27")

VENUE_OVERRIDES = {
    "愛知県国際展示場": [34.8613, 136.8123],
    "Aichi Sky Expo": [34.8613, 136.8123],
    "恵比寿ガーデンホール": [35.6421, 139.7132],
    "恵比寿ザ・ガーデンホール": [35.6421, 139.7132],
    "横浜アリーナ": [35.5175, 139.6172],
}

@st.cache_data
def get_location_cached(place_name):
    if not place_name:
        return None
    if place_name in VENUE_OVERRIDES:
        return VENUE_OVERRIDES[place_name]
    try:
        time.sleep(1)
        location = geolocator.geocode(place_name)
        if location:
            return location.latitude, location.longitude
    except:
        return None
    return None

def upload_photo_to_cloudinary(uploaded_file):
    if uploaded_file is None:
        return None
    try:
        image_bytes = uploaded_file.getvalue()
        response = cloudinary.uploader.upload(
            image_bytes, 
            folder="live_app_photos",
            resource_type="image"
        )
        return response['secure_url']
    except Exception as e:
        return f"ERROR: {e}"

# --- 4. データの読み書き（編集・削除対応） ---
def load_data():
    try:
        # 全データを取得（辞書形式のリスト）
        data = worksheet.get_all_records()
        
        # 🆕 ここが重要！スプレッドシート上の「行番号」を付与する
        # ヘッダーが1行目なので、データは2行目から始まる (index + 2)
        for i, row in enumerate(data):
            row['_row_index'] = i + 2
            
        df = pd.DataFrame(data)
        
        if not df.empty:
            df.columns = df.columns.str.strip()

        required_cols = ["日付", "ライブ名", "アーティスト", "会場名", "感想", "写真", "lat", "lon"]
        
        if df.empty:
            # カラムだけの空DF作成（_row_indexも含める）
            cols = required_cols + ['_row_index']
            return pd.DataFrame(columns=cols)
        
        for col in required_cols:
            if col not in df.columns:
                df[col] = None
        
        if "lat" in df.columns:
            df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
            df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
        
        if "日付" in df.columns and not df.empty:
            df = df.sort_values("日付", ascending=False)
            
        return df
    except Exception as e:
        st.error(f"データ読み込みエラー: {e}")
        return pd.DataFrame()

def add_record(record_dict):
    row = [
        str(record_dict["日付"]),
        record_dict["ライブ名"],
        record_dict["アーティスト"],
        record_dict["会場名"],
        record_dict["感想"],
        record_dict["写真"],
        record_dict["lat"],
        record_dict["lon"]
    ]
    worksheet.append_row(row)
    st.cache_data.clear()

def update_record(row_index, record_dict):
    # スプレッドシートの指定行を更新（範囲指定で一括更新）
    # A列〜H列を更新
    cell_range = f"A{row_index}:H{row_index}"
    values = [[
        str(record_dict["日付"]),
        record_dict["ライブ名"],
        record_dict["アーティスト"],
        record_dict["会場名"],
        record_dict["感想"],
        record_dict["写真"],
        record_dict["lat"],
        record_dict["lon"]
    ]]
    worksheet.update(range_name=cell_range, values=values)
    st.cache_data.clear()

def delete_records(row_indices):
    # 下の行から削除しないと、行番号がずれてしまうため降順にソート
    sorted_indices = sorted(row_indices, reverse=True)
    for idx in sorted_indices:
        worksheet.delete_rows(idx)
    st.cache_data.clear()

# --- 5. アプリ本体 ---
if 'data' not in st.session_state:
    st.session_state.data = load_data()

df = st.session_state.data

# --- サイドバー ---
st.sidebar.title("🛠️ メニュー")

with st.sidebar.expander("🏠 拠点の入力", expanded=True):
    user_home_name = st.text_input("自宅住所 または 最寄り駅", placeholder="例：東京駅")
    
    home_coords = DEFAULT_HOME_COORDS
    home_display_name = "東京駅（デフォルト）"
    
    if user_home_name:
        found_coords = get_location_cached(user_home_name)
        if found_coords:
            home_coords = found_coords
            home_display_name = user_home_name
            st.success(f"📍 {user_home_name} を設定しました")
        else:
            st.warning("場所が見つかりませんでした。")

st.sidebar.divider()
st.sidebar.header("📝 新規参戦記録")

with st.sidebar.form("entry_form"):
    date = st.date_input("日付", key="input_date", value=datetime.date.today())
    live_name = st.text_input("ライブ名・ツアー名", key="input_live")
    artist = st.text_input("アーティスト名", key="input_artist")
    venue = st.text_input("会場名", placeholder="例：横浜アリーナ", key="input_venue")
    photo = st.file_uploader("思い出の写真", type=["jpg", "png", "jpeg"], key=st.session_state["uploader_key"])
    comment = st.text_area("一言感想", key="input_comment")
    
    submitted = st.form_submit_button("記録 (Cloud保存)")

    if submitted:
        if not venue or not artist:
            st.error("⚠️ アーティスト名と会場名は必須です！")
        else:
            with st.spinner("位置特定＆写真保存中..."):
                coords = get_location_cached(venue)
                if coords:
                    photo_url = "None"
                    
                    if photo:
                        result = upload_photo_to_cloudinary(photo)
                        if result and str(result).startswith("ERROR"):
                            st.error(f"❌ 写真の保存に失敗しました: {result}")
                            st.stop()
                        else:
                            photo_url = result
                    
                    new_record = {
                        "日付": date,
                        "ライブ名": live_name,
                        "アーティスト": artist,
                        "会場名": venue,
                        "感想": comment,
                        "写真": photo_url,
                        "lat": coords[0],
                        "lon": coords[1]
                    }
                    add_record(new_record)
                    st.success("✅ 保存成功！")
                    st.session_state.data = load_data()
                    st.session_state["should_clear_form"] = True
                    st.rerun()
                else:
                    st.error(f"⚠️ 「{venue}」の場所が見つかりません。")

# --- メイン画面 ---
if not df.empty:
    tab1, tab2 = st.tabs(["🗺️ マップ", "📊 記録リスト"])

    with tab1:
        total_distance_km = 0
        for index, row in df.iterrows():
            if pd.notnull(row['lat']) and pd.notnull(row['lon']):
                venue_loc = (row['lat'], row['lon'])
                dist = geodesic(home_coords, venue_loc).km * 2
                total_distance_km += dist
        
        col1, col2 = st.columns(2)
        col1.metric("🎫 総参戦数", f"{len(df)} 回")
        col2.metric(f"🚗 総移動距離", f"{int(total_distance_km):,} km")
        st.markdown("---")

        center_lat = df['lat'].mean()
        center_lon = df['lon'].mean()
        m = folium.Map(location=[center_lat, center_lon], zoom_start=5)
        
        folium.Marker(
            location=[home_coords[0], home_coords[1]],
            popup="ここから移動！",
            tooltip=f"拠点: {home_display_name}",
            icon=folium.Icon(color="blue", icon="home")
        ).add_to(m)

        grouped = df.groupby('会場名')
        for venue_name, group in grouped:
            lat = group.iloc[0]['lat']
            lon = group.iloc[0]['lon']
            count = len(group)
            
            html = f"""
            <div style="font-family:sans-serif; width:300px; max-height:300px; overflow-y:auto;">
                <h4 style="color:#E63946; margin-bottom:5px; position:sticky; top:0; background:white; z-index:1;">
                    <b>{venue_name}</b>
                </h4>
                <p><b>🏆 参戦回数: {count}回</b></p>
                <hr>
            """
            
            group = group.sort_values('日付', ascending=False)
            for _, row in group.iterrows():
                img_tag = ""
                photo_val = str(row.get("写真", ""))
                if photo_val and photo_val != "None" and photo_val.startswith("http"):
                    img_tag = f'<img src="{photo_val}" style="width:100%; border-radius:5px; margin-bottom:5px;">'
                
                live_text = row.get('ライブ名', '') or ""

                html += f"""
                <div style="margin-bottom:15px; background:#f9f9f9; padding:10px; border-radius:5px;">
                    📅 {row['日付']}<br>
                    🎤 <b>{row['アーティスト']}</b><br>
                    🎵 {live_text}<br>
                    {img_tag}
                    💬 {row['感想']}<br>
                </div>
                """
            html += "</div>"
            
            folium.Marker(
                location=[lat, lon],
                popup=folium.Popup(html, max_width=320),
                tooltip=f"{venue_name} ({count}回)",
                icon=folium.Icon(color="red", icon="music")
            ).add_to(m)
        
        st_folium(m, width=800, height=500, use_container_width=True, returned_objects=[])

    with tab2:
        st.write("### 📝 データの管理")
        st.info("💡 左端のチェックボックスを選択すると、編集・削除メニューが表示されます。")
        
        # ユーザーに見せるカラム（行番号などは隠す）
        display_cols = ["日付", "ライブ名", "アーティスト", "会場名", "感想", "写真"]
        
        # データフレームを表示 & 選択機能
        event = st.dataframe(
            df[display_cols],
            on_select="rerun",
            selection_mode="multi-row",
            hide_index=True,
            use_container_width=True
        )

        # 選択された行のインデックスを取得
        selected_rows = event.selection.rows
        
        if selected_rows:
            # 選択されたデータを取得
            selected_df = df.iloc[selected_rows]
            st.markdown("---")
            
            # --- 削除機能（1件以上選択で表示） ---
            if st.button(f"🗑️ 選択した {len(selected_rows)} 件を削除する", type="primary"):
                # 削除対象の行番号リストを取得
                target_indices = selected_df['_row_index'].tolist()
                
                with st.spinner("削除中..."):
                    delete_records(target_indices)
                    st.success("削除しました！")
                    st.session_state.data = load_data()
                    st.rerun()

            # --- 編集機能（1件選択時のみ表示） ---
            if len(selected_rows) == 1:
                st.markdown("#### ✏️ 編集モード")
                
                # 編集対象のデータを取り出す
                target_row = selected_df.iloc[0]
                target_sheet_index = target_row['_row_index'] # スプレッドシートの行番号
                
                with st.form("edit_form"):
                    # 日付の変換
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
                            # 座標の再取得（会場名が変わった場合）
                            new_lat, new_lon = target_row["lat"], target_row["lon"]
                            if e_venue != target_row["会場名"]:
                                coords = get_location_cached(e_venue)
                                if coords:
                                    new_lat, new_lon = coords
                            
                            # 写真の再アップロード
                            new_photo_url = target_row["写真"]
                            if e_photo:
                                res = upload_photo_to_cloudinary(e_photo)
                                if res and not str(res).startswith("ERROR"):
                                    new_photo_url = res
                            
                            # 更新データ作成
                            updated_record = {
                                "日付": e_date,
                                "ライブ名": e_live,
                                "アーティスト": e_artist,
                                "会場名": e_venue,
                                "感想": e_comment,
                                "写真": new_photo_url,
                                "lat": new_lat,
                                "lon": new_lon
                            }
                            
                            # スプレッドシート更新実行
                            update_record(target_sheet_index, updated_record)
                            st.success("更新しました！")
                            st.session_state.data = load_data()
                            st.rerun()