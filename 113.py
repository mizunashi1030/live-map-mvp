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
import base64
import datetime

# --- Google連携用ライブラリ ---
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# --- 1. アプリの設定 ---
st.set_page_config(page_title="ライブ参戦記録 & 推し活マップ", layout="wide")
st.title("🎸 ライブ参戦記録 & 推し活マップ")

# デフォルトの拠点（東京駅）
DEFAULT_HOME_COORDS = (35.6812, 139.7671)

# --- 🆕 フォームリセット処理（最優先で実行） ---
# ここで「リセットフラグ」が立っているかを確認し、立っていれば初期化します。
# ウィジェットが描画される「前」に値をセットするため、エラーになりません。
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
    st.session_state["uploader_key"] = str(time.time()) # キーを変えてアップローダーをリセット
    st.session_state["should_clear_form"] = False # フラグを下ろす

# --- 2. Google認証 & データ取得関数 ---
@st.cache_resource
def init_google_services():
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"], scopes=scopes
        )
        gc = gspread.authorize(creds)
        drive_service = build('drive', 'v3', credentials=creds)
        return gc, drive_service
    except Exception as e:
        return None, None

gc, drive_service = init_google_services()

if gc is None:
    st.error("⚠️ Google連携エラー: secrets.tomlの設定を確認してください。")
    st.stop()

try:
    spreadsheet_id = st.secrets["app_config"]["spreadsheet_id"]
    drive_folder_id = st.secrets["app_config"]["drive_folder_id"]
    sh = gc.open_by_key(spreadsheet_id)
    worksheet = sh.sheet1
except Exception as e:
    st.error(f"⚠️ スプレッドシートへの接続エラー: {e}")
    st.stop()

# --- 3. ヘルパー関数たち ---
geolocator = Nominatim(user_agent="my_live_app_mvp_v21")

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

def upload_photo_to_drive(uploaded_file):
    if uploaded_file is None:
        return None
    try:
        image = Image.open(uploaded_file)
        image = ImageOps.exif_transpose(image)
        image.thumbnail((800, 800))
        
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=70)
        output.seek(0)
        
        file_metadata = {
            'name': f"{int(time.time())}_{uploaded_file.name}",
            'parents': [drive_folder_id]
        }
        media = MediaIoBaseUpload(output, mimetype='image/jpeg', resumable=True)
        
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()
        
        return file.get('id')
    except Exception as e:
        st.error(f"アップロードエラー: {e}")
        return None

@st.cache_data(ttl=3600)
def get_drive_image_base64(file_id):
    if not file_id or file_id == "None":
        return None
    try:
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        request.execute() 
        fh.write(request.execute())
        
        fh.seek(0)
        img = Image.open(fh)
        img.thumbnail((300, 300))
        
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG")
        encoded = base64.b64encode(buffered.getvalue()).decode()
        return f"data:image/jpeg;base64,{encoded}"
    except:
        return None

# --- 4. データの読み書き ---
def load_data():
    try:
        data = worksheet.get_all_records()
        df = pd.DataFrame(data)
        
        required_cols = ["日付", "ライブ名", "アーティスト", "会場名", "感想", "写真", "lat", "lon"]
        
        if df.empty:
            return pd.DataFrame(columns=required_cols)
        
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
        st.error(f"データ読み込み中にエラーが発生しました: {e}")
        return pd.DataFrame(columns=["日付", "ライブ名", "アーティスト", "会場名", "感想", "写真", "lat", "lon"])

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

# --- 5. アプリ本体 ---
if 'data' not in st.session_state:
    st.session_state.data = load_data()

df = st.session_state.data

# --- サイドバー ---
st.sidebar.title("🛠️ メニュー")

with st.sidebar.expander("🏠 拠点の入力", expanded=True):
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
            st.warning("場所が見つかりませんでした。デフォルトを使用します。")

st.sidebar.divider()

st.sidebar.header("📝 新規参戦記録")

with st.sidebar.form("entry_form"):
    # session_stateにあればそれを初期値として使う（リセット直後は空になる）
    date = st.date_input("日付", key="input_date", value=datetime.date.today())
    live_name = st.text_input("ライブ名・ツアー名", key="input_live")
    artist = st.text_input("アーティスト名", key="input_artist")
    venue = st.text_input("会場名", placeholder="例：横浜アリーナ", key="input_venue")
    photo = st.file_uploader("思い出の写真", type=["jpg", "png", "jpeg"], key=st.session_state["uploader_key"])
    comment = st.text_area("一言感想", key="input_comment")
    
    submitted = st.form_submit_button("記録 ")

    if submitted:
        if not venue or not artist:
            st.error("⚠️ アーティスト名と会場名は必須です！")
        else:
            with st.spinner("位置特定＆Googleドライブに保存中..."):
                coords = get_location_cached(venue)
                if coords:
                    photo_id = "None"
                    if photo:
                        photo_id = upload_photo_to_drive(photo)
                    
                    new_record = {
                        "日付": date,
                        "ライブ名": live_name,
                        "アーティスト": artist,
                        "会場名": venue,
                        "感想": comment,
                        "写真": photo_id,
                        "lat": coords[0],
                        "lon": coords[1]
                    }
                    add_record(new_record)
                    st.success("✅ スプレッドシートに保存しました！")
                    st.session_state.data = load_data()
                    
                    # 🆕 ここを変更！
                    # 直接消すのではなく「次回消してねフラグ」を立ててリロードする
                    st.session_state["should_clear_form"] = True
                    st.rerun()
                else:
                    st.error(f"⚠️ 「{venue}」の場所が見つかりません。正式名称で試してください。")

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
                if row.get("写真") and row["写真"] != "None":
                    b64 = get_drive_image_base64(row["写真"])
                    if b64:
                        img_tag = f'<img src="{b64}" style="width:100%; border-radius:5px; margin-bottom:5px;">'
                
                html += f"""
                <div style="margin-bottom:15px; background:#f9f9f9; padding:10px; border-radius:5px;">
                    📅 {row['日付']}<br>
                    🎤 <b>{row['アーティスト']}</b><br>
                    🎵 {row['ライブ名']}<br>
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
        st.write("### 📜 参戦リスト")
        st.dataframe(df, hide_index=True, use_container_width=True)
        
        if st.button("🔄 データを再読み込み"):
            st.session_state.data = load_data()
            st.rerun()