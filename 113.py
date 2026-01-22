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
st.title("🎸 ライブ参戦記録 & 推し活マップ (Multi-User)")

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
        pass 
    
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
geolocator = Nominatim(user_agent="my_live_app_mvp_v29")

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

# --- 4. データの読み書き（ユーザーID対応） ---
def load_data(current_user_id):
    try:
        # 全データを取得
        data = worksheet.get_all_records()
        
        # 行番号を付与
        for i, row in enumerate(data):
            row['_row_index'] = i + 2
            
        df = pd.DataFrame(data)
        
        if not df.empty:
            df.columns = df.columns.str.strip()

        # 必要なカラム（ユーザーIDを追加）
        required_cols = ["日付", "ライブ名", "アーティスト", "会場名", "感想", "写真", "lat", "lon", "ユーザーID"]
        
        if df.empty:
            cols = required_cols + ['_row_index']
            return pd.DataFrame(columns=cols)
        
        # 足りない列があれば作る
        for col in required_cols:
            if col not in df.columns:
                df[col] = None # 文字列としてNoneを入れておく
        
        # 数値変換
        if "lat" in df.columns:
            df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
            df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
        
        # 🆕 ここで自分のデータだけに絞り込む！
        # ユーザーIDが空のデータは表示しない（過去のデータは誰のものでもない扱いになる）
        # もし過去データを自分のにしたい場合は、スプレッドシート上で手動でIDを入れてください
        if current_user_id:
            # 文字列型にして比較（念のため）
            df["ユーザーID"] = df["ユーザーID"].astype(str)
            df = df[df["ユーザーID"] == str(current_user_id)]
        else:
            # ユーザーID未指定時は空を返す（安全策）
            return pd.DataFrame(columns=required_cols + ['_row_index'])

        if "日付" in df.columns and not df.empty:
            df = df.sort_values("日付", ascending=False)
            
        return df
    except Exception as e:
        st.error(f"データ読み込みエラー: {e}")
        return pd.DataFrame()

def add_record(record_dict):
    # 保存する列の順番を固定
    row = [
        str(record_dict["日付"]),
        record_dict["ライブ名"],
        record_dict["アーティスト"],
        record_dict["会場名"],
        record_dict["感想"],
        record_dict["写真"],
        record_dict["lat"],
        record_dict["lon"],
        record_dict["ユーザーID"] # 🆕 追加
    ]
    worksheet.append_row(row)
    st.cache_data.clear()

def update_record(row_index, record_dict):
    # I列まで更新範囲を広げる
    cell_range = f"A{row_index}:I{row_index}"
    values = [[
        str(record_dict["日付"]),
        record_dict["ライブ名"],
        record_dict["アーティスト"],
        record_dict["会場名"],
        record_dict["感想"],
        record_dict["写真"],
        record_dict["lat"],
        record_dict["lon"],
        record_dict["ユーザーID"] # 🆕 追加
    ]]
    worksheet.update(range_name=cell_range, values=values)
    st.cache_data.clear()

def delete_records(row_indices):
    sorted_indices = sorted(row_indices, reverse=True)
    for idx in sorted_indices:
        worksheet.delete_rows(idx)
    st.cache_data.clear()

# --- 5. アプリ本体 ---

# サイドバー：ログイン機能
st.sidebar.title("👤 ユーザー設定")

# session_stateにユーザーIDを保存
if "user_id" not in st.session_state:
    st.session_state["user_id"] = ""

# 入力欄
input_user_id = st.sidebar.text_input("ユーザー名（ID）を入力", value=st.session_state["user_id"], placeholder="例: taro123")

# 入力されたらsession_stateを更新
if input_user_id:
    st.session_state["user_id"] = input_user_id.strip()

current_user = st.session_state["user_id"]

if not current_user:
    st.warning("👈 左のサイドバーで「ユーザー名」を入力してください。")
    st.info("💡 ユーザー名ごとにデータが保存されます。友達と被らない名前推奨です！")
    st.stop() # ここで処理を止める（ログイン必須）

st.sidebar.success(f"ログイン中: **{current_user}**")
st.sidebar.divider()

# --- データ読み込み ---
if 'data' not in st.session_state:
    st.session_state.data = load_data(current_user)
else:
    # ユーザーが変わった場合などを考慮してリロード判定を入れてもいいが、
    # 簡易的に毎回load_dataを呼ぶ形にする（引数が変わればcacheが効いてても再取得される設計ならOKだが、
    # ここでは明示的に再取得ボタンを押させる運用にするか、rerunで更新される）
    st.session_state.data = load_data(current_user)

df = st.session_state.data

# --- サイドバー：拠点登録 ---
with st.sidebar.expander("🏠 拠点の入力", expanded=True):
    # ユーザーごとの設定保存はまだDBにテーブルがないので、簡易的に入力させる（毎回入力が必要になるがMVPなので許容）
    # ※ 本格化するなら「ユーザー設定テーブル」を作る必要があります
    user_home_name = st.text_input("自宅住所 または 最寄り駅", placeholder="例：新大阪駅")
    
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
                        "lon": coords[1],
                        "ユーザーID": current_user # 🆕 保存
                    }
                    add_record(new_record)
                    st.success("✅ 保存成功！")
                    st.session_state.data = load_data(current_user)
                    st.session_state["should_clear_form"] = True
                    st.rerun()
                else:
                    st.error(f"⚠️ 「{venue}」の場所が見つかりません。")

# --- メイン画面 ---
if df.empty:
    st.info(f"👋 こんにちは、**{current_user}** さん！\nまだ記録がありません。左のサイドバーから最初のライブ記録を追加してみましょう！")
else:
    tab1, tab2 = st.tabs(["🗺️ マップ & 実績", "📊 分析 & 記録管理"])

    with tab1:
        total_distance_km = 0
        for index, row in df.iterrows():
            if pd.notnull(row['lat']) and pd.notnull(row['lon']):
                venue_loc = (row['lat'], row['lon'])
                dist = geodesic(home_coords, venue_loc).km * 2
                total_distance_km += dist
        
        col1, col2 = st.columns(2)
        col1.metric("🎫 総参戦数", f"{len(df)} 回")
        col2.metric(f"🚗 総移動距離（{home_display_name}発）", f"{int(total_distance_km):,} km")
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
        st.write("### 🎨 アーティスト別 参戦割合")
        if "アーティスト" in df.columns:
            artist_counts = df['アーティスト'].value_counts().reset_index()
            artist_counts.columns = ['アーティスト', '回数']
            
            col_chart, col_rank = st.columns([0.6, 0.4])
            with col_chart:
                fig = px.pie(artist_counts, values='回数', names='アーティスト', title='参戦割合チャート')
                st.plotly_chart(fig, use_container_width=True)
            with col_rank:
                st.dataframe(artist_counts, hide_index=True)
        
        st.markdown("---")
        st.write("### 📝 データの管理")
        st.info("💡 左端のチェックボックスを選択すると、編集・削除メニューが表示されます。")
        
        display_cols = ["日付", "ライブ名", "アーティスト", "会場名", "感想", "写真"]
        
        event = st.dataframe(
            df[display_cols],
            on_select="rerun",
            selection_mode="multi-row",
            hide_index=True,
            use_container_width=True
        )

        selected_rows = event.selection.rows
        
        if selected_rows:
            selected_df = df.iloc[selected_rows]
            st.markdown("---")
            
            if st.button(f"🗑️ 選択した {len(selected_rows)} 件を削除する", type="primary"):
                target_indices = selected_df['_row_index'].tolist()
                with st.spinner("削除中..."):
                    delete_records(target_indices)
                    st.success("削除しました！")
                    st.session_state.data = load_data(current_user)
                    st.rerun()

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
                                "ユーザーID": current_user # IDを引き継ぐ
                            }
                            
                            update_record(target_sheet_index, updated_record)
                            st.success("更新しました！")
                            st.session_state.data = load_data(current_user)
                            st.rerun()