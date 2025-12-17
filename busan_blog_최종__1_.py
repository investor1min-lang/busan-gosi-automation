# -*- coding: utf-8 -*-
# busan_blog_NAVER.py
"""
부산 고시공고 → 블로그 자동화 (네이버 API 버전)

카카오 → 네이버 API 완전 전환
✅ 심사 불필요, 즉시 사용 가능
✅ 한국 POI 데이터 더 풍부
"""

import os
import re
import csv
import time
import math
import requests
import urllib.parse
from pathlib import Path
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

try:
    import pyperclip
except:
    pyperclip = None

# ====== CONFIG ======
BASE_URL = "https://www.busan.go.kr/news/gosiboard?articlNo=2"
START_PAGE = 1
END_PAGE = 3  # 1페이지 → 3페이지로 확대
KEYWORDS = ["재개발", "재건축"]

# OS에 따라 경로 설정 (Windows/Linux 모두 지원)
import platform
if platform.system() == "Windows":
    OUT_DIR = r"C:\Users\송미승\downloaded_files"
    CSV_PATH = r"C:\Users\송미승\download_manifest.csv"
    TESSERACT_EXE = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
else:
    # Linux (GitHub Actions)
    OUT_DIR = os.path.join(os.getcwd(), "downloaded_files")
    CSV_PATH = os.path.join(os.getcwd(), "download_manifest.csv")
    TESSERACT_EXE = "/usr/bin/tesseract"

# 네이버 API (카카오에서 변경)
NAVER_CLIENT_ID = "1i3u9jg46o"
NAVER_CLIENT_SECRET = "6FcXzVbgEM"

HEADLESS_LIST = True
HEADLESS_MAP = False
PAGE_SLEEP = 0.8
TIMEOUT = 15

# Poppler는 더 이상 사용하지 않음 (PyMuPDF 사용)
POPPLER_BIN = ""
OCR_MIN_CHARS = 300

# ====== 유틸 ======
def ensure_dirs():
    for p in [OUT_DIR, os.path.join(OUT_DIR, "txt"), os.path.join(OUT_DIR, "blog_html"),
              os.path.join(OUT_DIR, "pdf_images"), os.path.join(OUT_DIR, "maps")]:
        Path(p).mkdir(parents=True, exist_ok=True)

def make_driver(headless=True):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,1100")
    return webdriver.Chrome(options=opts)

def safe_text(el):
    try:
        return el.text.strip()
    except:
        return ""

def clean_filename(name):
    name = re.sub(r"\s*\(용량[^)]*\)\s*$", "", name or "").strip()
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return (name or "unnamed")[:180]

def normalize_text(text):
    t = (text or "").lower()
    t = re.sub(r"\s+", " ", t)
    return t.strip()

# ====== 네이버 API ======
def naver_geocode(address):
    """네이버: 주소 → 좌표"""
    try:
        # 부산이 없으면 추가
        if "부산" not in address:
            address = f"부산 {address}"
        
        url = "https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode"
        headers = {
            "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
            "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
        }
        params = {"query": address}
        
        print(f"        🔍 주소 검색: {address}")
        
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        
        if resp.status_code != 200:
            print(f"        ❌ API 오류 (상태: {resp.status_code})")
            return None
        
        data = resp.json()
        
        if data.get('addresses') and len(data['addresses']) > 0:
            addr = data['addresses'][0]
            lat = float(addr['y'])
            lng = float(addr['x'])
            print(f"        ✅ 좌표: ({lat:.6f}, {lng:.6f})")
            return (lat, lng)
        
        # 실패 시 번지수 제거하고 재시도
        if "번지" in address:
            addr_without_benji = re.sub(r'\d+(?:-\d+)?번지', '', address).strip()
            print(f"        재시도: {addr_without_benji}")
            
            params = {"query": addr_without_benji}
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            data = resp.json()
            
            if data.get('addresses') and len(data['addresses']) > 0:
                addr = data['addresses'][0]
                lat = float(addr['y'])
                lng = float(addr['x'])
                print(f"        ✅ 좌표 (재시도): ({lat:.6f}, {lng:.6f})")
                return (lat, lng)
        
        print(f"        ❌ 좌표를 찾을 수 없습니다")
        return None
        
    except Exception as e:
        print(f"        ⚠️ Geocoding 오류: {e}")
        return None

def naver_search_places(keyword, center_lat, center_lng, radius=2000):
    """네이버: 키워드로 장소 검색"""
    try:
        # 네이버 Local Search API
        url = "https://naveropenapi.apigw.ntruss.com/map-place/v1/search"
        headers = {
            "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
            "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
        }
        
        # 검색어에 부산 추가
        query = f"부산 {keyword}"
        
        params = {
            "query": query,
            "coordinate": f"{center_lng},{center_lat}",  # 네이버는 경도,위도 순서
            "display": 5  # 최대 5개
        }
        
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        
        if resp.status_code != 200:
            print(f"        ⚠️ API 오류 ({keyword}): {resp.status_code}")
            return []
        
        data = resp.json()
        places = []
        
        if data.get('places'):
            for place in data['places']:
                try:
                    place_lat = float(place['y'])
                    place_lng = float(place['x'])
                    
                    # 거리 계산 (Haversine formula)
                    distance = calculate_distance(center_lat, center_lng, place_lat, place_lng)
                    
                    # 반경 내 장소만 추가
                    if distance <= radius:
                        places.append({
                            'name': place.get('name', ''),
                            'distance': int(distance),
                            'lat': place_lat,
                            'lng': place_lng
                        })
                except:
                    continue
        
        return places
        
    except Exception as e:
        print(f"        ⚠️ 검색 실패 ({keyword}): {e}")
        return []

def calculate_distance(lat1, lng1, lat2, lng2):
    """두 좌표 사이의 거리 계산 (미터)"""
    from math import radians, sin, cos, sqrt, atan2
    
    R = 6371000  # 지구 반지름 (미터)
    
    lat1_rad = radians(lat1)
    lat2_rad = radians(lat2)
    delta_lat = radians(lat2 - lat1)
    delta_lng = radians(lng2 - lng1)
    
    a = sin(delta_lat/2)**2 + cos(lat1_rad) * cos(lat2_rad) * sin(delta_lng/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    
    return R * c

def collect_pois_naver(address):
    """네이버로 POI 수집"""
    print(f"\n    🔍 네이버 POI 수집 시작")
    print(f"    주소: {address}")
    
    coords = naver_geocode(address)
    if not coords:
        print(f"    ❌ 좌표 변환 실패 - POI 수집 건너뜀")
        return None
    
    center_lat, center_lng = coords
    print(f"    ✅ 좌표: ({center_lat:.6f}, {center_lng:.6f})")
    
    # 카테고리별 키워드 (네이버에 최적화)
    categories = {
        "지하철역": ["지하철역", "전철역"],
        "초등학교": ["초등학교"],
        "중학교": ["중학교"],
        "대형마트": ["이마트", "롯데마트", "홈플러스"],
        "관광지": ["해수욕장", "공원"],
    }
    
    pois = {}
    total_found = 0
    
    for cat_name, keywords in categories.items():
        all_places = []
        for kw in keywords:
            places = naver_search_places(kw, center_lat, center_lng, radius=2000)
            all_places.extend(places)
            time.sleep(0.1)  # API 호출 간격
        
        # 중복 제거
        seen = set()
        unique = []
        for place in all_places:
            if place['name'] not in seen:
                seen.add(place['name'])
                unique.append(place)
        
        unique.sort(key=lambda x: x['distance'])
        if unique:
            pois[cat_name] = unique
            total_found += len(unique)
            print(f"    ✅ {cat_name}: {len(unique)}개 ({unique[0]['name']} {unique[0]['distance']}m)")
        else:
            print(f"    ⚪ {cat_name}: 0개")
    
    if total_found > 0:
        print(f"    📊 총 {total_found}개 POI 수집 완료")
        return pois
    else:
        print(f"    ⚠️ POI를 하나도 찾지 못했습니다")
        return None

# ====== 1. 목록 수집 ======
def collect_posts(driver):
    urls = []
    seen_datano = set()  # dataNo 기반 중복 체크 추가
    
    for page in range(START_PAGE, END_PAGE + 1):
        url = f"{BASE_URL}&curPage={page}"
        print(f"\n▶ 페이지 {page}/{END_PAGE}")
        
        driver.get(url)
        time.sleep(PAGE_SLEEP)
        
        try:
            WebDriverWait(driver, TIMEOUT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr"))
            )
        except:
            continue
        
        rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
        print(f"  📋 전체 행 수: {len(rows)}")
        
        for idx, row in enumerate(rows, 1):
            try:
                links = row.find_elements(By.TAG_NAME, "a")
                title_link = None
                
                for link in links:
                    txt = safe_text(link)
                    if txt and txt not in {"미리보기", "미리듣기"}:
                        title_link = link
                        break
                
                if not title_link:
                    continue
                
                title = safe_text(title_link)
                norm = normalize_text(title)
                
                # 디버깅: 모든 공고 출력
                has_keyword = any(kw in norm for kw in KEYWORDS)
                status = "✅" if has_keyword else "⊘"
                print(f"  {status} {title[:50]}")
                
                if has_keyword:
                    href = title_link.get_attribute("href")
                    
                    if href:
                        # dataNo 기반 중복 체크
                        datano_match = re.search(r'dataNo=(\d+)', href)
                        if datano_match:
                            datano = datano_match.group(1)
                            
                            if datano not in seen_datano:
                                seen_datano.add(datano)
                                urls.append(href)
                            else:
                                print(f"     ⊘ 중복 (dataNo: {datano})")
            except:
                continue
    
    print(f"\n📌 총 {len(urls)}개")
    return urls

# ====== 2. 상세 추출 ======
def extract_detail(driver, url):
    driver.get(url)
    time.sleep(0.5)
    WebDriverWait(driver, TIMEOUT).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    
    title = ""
    try:
        t = driver.find_element(By.XPATH, "//dl[contains(@class,'form-data-info')]//dt[normalize-space()='제목']/following-sibling::dd[1]//li[1]")
        title = safe_text(t)
    except:
        try:
            title = safe_text(driver.find_element(By.XPATH, "//h4[contains(@class,'form-data-subject')]"))
        except:
            pass
    
    attachments = []
    try:
        attach = driver.find_element(By.XPATH, "//dt[normalize-space()='첨부파일']/following-sibling::dd[1]")
        anchors = attach.find_elements(By.TAG_NAME, "a")
        
        for a in anchors:
            txt = safe_text(a)
            if txt in {"미리보기", "미리듣기"}:
                continue
            
            href = a.get_attribute("href")
            if href and "/comm/getFile" in href:
                attachments.append({
                    "filename": txt,
                    "url": href if href.startswith("http") else f"https://www.busan.go.kr{href}"
                })
    except:
        pass
    
    return {"url": url, "title": title, "attachments": attachments}

# ====== 3. 다운로드 ======
def download_pdf(driver, files, referer, title):
    saved = []
    cookies = requests.cookies.RequestsCookieJar()
    for c in driver.get_cookies():
        cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
    
    # SSL 우회 설정
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    for idx, f in enumerate(files, 1):
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                headers = {
                    "Referer": referer,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
                
                resp = requests.get(
                    f["url"], 
                    cookies=cookies, 
                    headers=headers,
                    timeout=60,  # 타임아웃 증가
                    stream=True,
                    verify=False  # SSL 검증 우회
                )
                resp.raise_for_status()
                
                prefix = datetime.now().strftime("%Y%m%d")
                filename = f"{prefix}_{clean_filename(title)[:50]}_{idx}.pdf"
                path = os.path.join(OUT_DIR, filename)
                
                with open(path, "wb") as fp:
                    for chunk in resp.iter_content(8192):
                        fp.write(chunk)
                
                saved.append(path)
                print(f"    ✅ {Path(path).name}")
                break  # 성공 시 루프 탈출
                
            except Exception as e:
                retry_count += 1
                if retry_count < max_retries:
                    print(f"    ⚠️ 재시도 {retry_count}/{max_retries}...")
                    time.sleep(2)  # 2초 대기 후 재시도
                else:
                    print(f"    ✗ 다운로드 실패 ({max_retries}회 시도): {e}")
    
    return saved

# ====== 4. PDF → 이미지 ======
def pdf_to_images(pdf_path, title):
    """
    PyMuPDF(fitz)로 PDF → 이미지 변환 (Poppler 불필요!)
    """
    try:
        import fitz  # PyMuPDF
    except:
        print("    ⚠️ PyMuPDF 미설치 (pip install PyMuPDF)")
        return []
    
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"    ⚠️ PDF 열기 실패: {e}")
        return []
    
    img_dir = Path(OUT_DIR) / "pdf_images" / clean_filename(title)
    img_dir.mkdir(parents=True, exist_ok=True)
    
    saved = []
    for page_num in range(len(doc)):
        try:
            page = doc.load_page(page_num)
            # 200 DPI로 렌더링 (matrix로 스케일 조정)
            mat = fitz.Matrix(200/72, 200/72)  # 72 DPI → 200 DPI
            pix = page.get_pixmap(matrix=mat)
            
            img_path = img_dir / f"page_{page_num + 1:03d}.png"
            pix.save(str(img_path))
            saved.append(str(img_path))
        except Exception as e:
            print(f"    ⚠️ 페이지 {page_num + 1} 변환 실패: {e}")
    
    doc.close()
    print(f"    ✅ PDF 이미지: {len(saved)}장")
    return saved

# ====== 5. OCR ======
def ocr_pdf(pdf_path):
    """
    PyMuPDF로 PDF 이미지 추출 후 OCR
    """
    try:
        import fitz  # PyMuPDF
        import pytesseract
        from PIL import Image
        import io
    except:
        return "", {}
    
    try:
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE
        doc = fitz.open(pdf_path)
        
        parts = []
        max_pages = min(5, len(doc))  # 최대 5페이지만 OCR
        
        for page_num in range(max_pages):
            page = doc.load_page(page_num)
            # 150 DPI로 렌더링
            mat = fitz.Matrix(150/72, 150/72)
            pix = page.get_pixmap(matrix=mat)
            
            # PIL Image로 변환
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data))
            
            # OCR 실행
            txt = pytesseract.image_to_string(img, lang="kor+eng")
            parts.append(txt)
        
        doc.close()
        text = "\n".join(parts)
        return text, {"chars": len(text)}
    except Exception as e:
        print(f"    ✗ OCR 실패: {e}")
        return "", {}

# ====== 6. 분석 ======
def analyze_text(text, title):
    info = {"type": "재건축" if "재건축" in title else "재개발"}
    
    # OCR 텍스트 전처리 - 줄바꿈 정리
    text_clean = re.sub(r'\s+', ' ', text)  # 모든 공백을 스페이스 하나로
    
    # 위치 - 다양한 패턴으로 추출
    found_addr = None
    
    # 패턴 1: "위치" 키워드 이후 찾기
    loc_after_label = re.search(r'위\s*치[:\s]*(.{5,80})', text_clean)
    if loc_after_label:
        after_text = loc_after_label.group(1)
        # 이 텍스트에서 주소 추출
        addr_patterns = [
            r'(부산(?:광역시)?\s*[가-힣]+구\s+[가-힣]+동\s+\d+(?:-\d+)?(?:\s*번지)?(?:\s*일원)?)',
            r'([가-힣]+구\s+[가-힣]+동\s+\d+(?:-\d+)?(?:\s*번지)?(?:\s*일원)?)',
        ]
        for p in addr_patterns:
            m = re.search(p, after_text)
            if m:
                found_addr = m.group(1).strip()
                print(f"    📍 주소 (위치 필드): {found_addr}")
                break
    
    # 패턴 2: 전체 텍스트에서 "구 + 동 + 번지" 직접 찾기
    if not found_addr:
        direct_patterns = [
            r'(부산(?:광역시)?\s*[가-힣]+구\s+[가-힣]+동\s+\d+(?:-\d+)?(?:\s*번지)?(?:\s*일원)?)',
            r'([가-힣]+구\s+[가-힣]+동\s+\d+(?:-\d+)?(?:\s*번지)?(?:\s*일원)?)',
        ]
        for p in direct_patterns:
            m = re.search(p, text_clean)
            if m:
                found_addr = m.group(1).strip()
                print(f"    📍 주소 (본문): {found_addr}")
                break
    
    # 패턴 3: 동 + 번지만 찾아서 조합
    if not found_addr:
        # 구 찾기
        gu_match = re.search(r'부산(?:광역시)?\s*([가-힣]+구)', text_clean)
        gu = gu_match.group(1) if gu_match else None
        
        # 동 + 번지 찾기
        dong_benji = re.search(r'([가-힣]+동)\s+(\d+(?:-\d+)?)\s*번지', text_clean)
        if dong_benji:
            dong = dong_benji.group(1)
            benji = dong_benji.group(2)
            
            if gu:
                found_addr = f"부산 {gu} {dong} {benji}번지"
            else:
                found_addr = f"부산 {dong} {benji}번지"
            
            print(f"    📍 주소 (조합): {found_addr}")
    
    # 패턴 4: 제목에서 동 추출 + 본문에서 구/번지 찾기
    if not found_addr:
        title_dong = re.search(r'([가-힣]+동)', title)
        if title_dong:
            dong = title_dong.group(1)
            
            # 본문에서 구 찾기
            gu_match = re.search(r'부산(?:광역시)?\s*([가-힣]+구)', text_clean)
            gu = gu_match.group(1) if gu_match else None
            
            # 본문에서 번지 찾기 (동 이름 근처)
            dong_idx = text_clean.find(dong)
            benji = None
            if dong_idx != -1:
                nearby = text_clean[max(0, dong_idx-50):dong_idx+100]
                benji_match = re.search(r'(\d+(?:-\d+)?)\s*번지', nearby)
                if benji_match:
                    benji = benji_match.group(1)
            
            # 조합
            if gu and benji:
                found_addr = f"부산 {gu} {dong} {benji}번지"
            elif gu:
                found_addr = f"부산 {gu} {dong}"
            else:
                found_addr = f"부산 {dong}"
            
            print(f"    📍 주소 (제목+본문): {found_addr}")
    
    if found_addr:
        # 최종 정리
        found_addr = re.sub(r'\s+', ' ', found_addr).strip()
        info["위치"] = found_addr
    else:
        print(f"    ⚠️ 주소 추출 실패")
    
    # 면적
    area_m = re.search(r"(?:구역)?면적[:\s]*([0-9,]+\.?\d*)\s*㎡", text)
    if area_m:
        info["면적"] = f"{area_m.group(1)}㎡"
    
    # 세대수
    house_m = re.search(r"(?:총\s*)?세대수[:\s]*([0-9,]+)", text)
    if house_m:
        info["세대수"] = f"{house_m.group(1)}세대"
    
    # 동수
    dong_m = re.search(r"([0-9]+)\s*개?\s*동", text)
    if dong_m:
        info["동수"] = f"{dong_m.group(1)}개동"
    
    # 층수
    floor_m = re.search(r"지하\s*(\d+).*지상\s*(\d+)", text)
    if floor_m:
        info["층수"] = f"지하{floor_m.group(1)}~지상{floor_m.group(2)}층"
    
    return info

# ====== 7. 네이버 지도 ======
def capture_naver_map(addr, title):
    print(f"    🗺️ 네이버 지도 캡처...")
    
    driver = None
    try:
        driver = make_driver(headless=HEADLESS_MAP)
        
        encoded_addr = urllib.parse.quote(addr)
        map_url = f"https://map.naver.com/v5/search/{encoded_addr}"
        
        driver.get(map_url)
        time.sleep(4)
        
        map_dir = Path(OUT_DIR) / "maps" / clean_filename(title)
        map_dir.mkdir(parents=True, exist_ok=True)
        
        # 로드맵
        road_path = str(map_dir / "naver_road.png")
        driver.save_screenshot(road_path)
        print(f"        ✅ 로드맵")
        
        # 위성
        sat_path = ""
        try:
            sat_btn = driver.find_element(By.CSS_SELECTOR, "button[title='위성'], button.btn_satellite")
            sat_btn.click()
            time.sleep(2)
            
            sat_path = str(map_dir / "naver_sat.png")
            driver.save_screenshot(sat_path)
            print(f"        ✅ 위성")
        except:
            print(f"        ⚠️ 위성 버튼 못 찾음")
        
        return map_url, [road_path, sat_path] if sat_path else [road_path]
    
    except Exception as e:
        print(f"        ✗ 실패: {e}")
        return "", []
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

# ====== 8. HTML 생성 ======
def build_html_with_poi(title, url, info, pdf_images, map_url, map_images, pois):
    """HTML 생성 (네이버 POI 포함)"""
    
    html = []
    
    location = info.get("위치", "부산")
    event_type = info.get("type", "재개발")
    
    area_match = re.search(r'([가-힣0-9]+구역|[가-힣]+동)', title)
    area_name = area_match.group(1) if area_match else location.split()[-1] if location else "부산"
    
    gu_match = re.search(r'([가-힣]+구)', location)
    gu_name = gu_match.group(1) if gu_match else ""
    
    main_keyword = f"{area_name} {event_type}"
    seo_title = f'{main_keyword} 정비구역 지정 | {gu_name} {event_type} 완벽정리'
    
    html.append(f'<h1 style="color: #1a1a1a; font-size: 24px; font-weight: bold; margin-bottom: 20px;">{seo_title}</h1>')
    
    # 인트로
    html.append('<div style="background: #f8f9fa; padding: 20px; border-radius: 8px; margin-bottom: 30px;">')
    html.append(f'<p style="font-size: 16px; line-height: 1.8; margin: 0;">')
    html.append(f'<strong>{location}</strong>에 <strong>{main_keyword}</strong> 정비구역 지정이 고시되었습니다.')
    html.append('</p></div>')
    
    # 목차
    html.append('<div style="background: #fff; border: 1px solid #e0e0e0; padding: 15px; border-radius: 8px; margin-bottom: 30px;">')
    html.append('<p style="font-weight: bold; margin-bottom: 10px;">목차</p>')
    html.append('<p style="font-size: 14px; line-height: 2.0; margin: 0;">')
    html.append('1. 위치<br>2. 사업 규모<br>3. 주변 시설 (2km 반경)<br>4. 구역 경계<br>5. 진행 일정')
    html.append('</p></div>')
    
    html.append('<hr style="border-top: 2px solid #e0e0e0; margin: 30px 0;">')
    
    # 1. 위치
    if info.get("위치"):
        html.append(f'<h2 style="color: #2c3e50; font-size: 20px; margin-top: 40px; font-weight: bold;">1. 어디에 지어지나요?</h2>')
        html.append(f'<p style="font-size: 15px; line-height: 1.8;">')
        html.append(f'<strong>{main_keyword}</strong>은 <strong>{info["위치"]}</strong>에 위치합니다.')
        html.append('</p>')
        
        if map_url:
            html.append(f'<p style="margin-top: 15px;"><a href="{map_url}" target="_blank" style="display: inline-block; padding: 10px 20px; background: #0066cc; color: white; text-decoration: none; border-radius: 5px;">네이버 지도로 보기</a></p>')
        
        html.append('<hr style="border-top: 1px solid #e0e0e0; margin: 30px 0;">')
    
    # 2. 사업 규모
    html.append(f'<h2 style="color: #2c3e50; font-size: 20px; margin-top: 40px; font-weight: bold;">2. 사업 규모는?</h2>')
    html.append('<div style="background: #f8f9fa; padding: 20px; border-radius: 8px;">')
    html.append('<ul style="line-height: 2.2; font-size: 15px; margin: 0;">')
    
    if info.get("면적"):
        html.append(f'<li><strong>면적:</strong> {info["면적"]}</li>')
    if info.get("세대수"):
        html.append(f'<li><strong>세대수:</strong> {info["세대수"]}</li>')
    if info.get("동수"):
        html.append(f'<li><strong>동수:</strong> {info["동수"]}</li>')
    if info.get("층수"):
        html.append(f'<li><strong>층수:</strong> {info["층수"]}</li>')
    
    html.append('</ul></div>')
    html.append('<hr style="border-top: 1px solid #e0e0e0; margin: 30px 0;">')
    
    # 3. 주변 시설 (네이버 POI)
    if pois:
        html.append(f'<h2 style="color: #2c3e50; font-size: 20px; margin-top: 40px; font-weight: bold;">3. 주변 시설 (2km 반경)</h2>')
        
        # 깔끔한 표
        html.append("""
<style>
.poi-table {
    border-collapse: collapse;
    width: auto;
    margin: 20px auto;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
}
.poi-table th {
    background: #03C75A;
    color: white;
    padding: 12px 20px;
    text-align: center;
    font-weight: bold;
    border: 1px solid #ddd;
}
.poi-table td {
    border: 1px solid #ddd;
    padding: 10px 20px;
}
.poi-table td:first-child {
    text-align: center;
    font-weight: bold;
    color: #2c3e50;
}
.poi-table td:nth-child(2) {
    text-align: left;
}
.poi-table td:last-child {
    text-align: center;
    font-weight: bold;
    color: #e74c3c;
}
.poi-table tr:nth-child(even) {
    background: #f8f9fa;
}
</style>
""")
        
        html.append('<table class="poi-table">')
        html.append('<tr><th>카테고리</th><th>이름</th><th>거리(m)</th></tr>')
        
        for cat, places in pois.items():
            if places:
                place = places[0]
                html.append('<tr>')
                html.append(f'<td>{cat}</td>')
                html.append(f'<td>{place["name"]}</td>')
                html.append(f'<td>{place["distance"]}</td>')
                html.append('</tr>')
        
        html.append('</table>')
        html.append('<p style="text-align: center; color: #7f8c8d; font-size: 0.9em;">※ 네이버 지도 기준</p>')
        
        html.append('<hr style="border-top: 1px solid #e0e0e0; margin: 30px 0;">')
    
    # 4. 구역 경계
    if pdf_images:
        html.append(f'<h2 style="color: #2c3e50; font-size: 20px; margin-top: 40px; font-weight: bold;">4. 구역 경계는?</h2>')
        html.append('<p style="color: #666; font-size: 14px;">※ PDF 이미지를 블로그에 업로드하세요</p>')
        html.append(f'<p style="color: #999; font-size: 13px;">위치: pdf_images 폴더 (총 {len(pdf_images)}장)</p>')
        html.append('<hr style="border-top: 1px solid #e0e0e0; margin: 30px 0;">')
    
    # 5. 진행 일정
    html.append(f'<h2 style="color: #2c3e50; font-size: 20px; margin-top: 40px; font-weight: bold;">5. 앞으로 어떻게 진행되나요?</h2>')
    html.append('<ol style="line-height: 2.2; font-size: 15px;">')
    html.append('<li><strong>현재:</strong> 정비구역 지정 고시</li>')
    html.append('<li><strong>다음:</strong> 추진위원회 구성 → 조합 설립</li>')
    html.append('<li><strong>예상:</strong> 조합설립 후 약 5~7년</li>')
    html.append('</ol>')
    
    html.append('<div style="background: #e8f5e9; padding: 15px; border-radius: 8px; margin-top: 15px;">')
    html.append(f'<p style="font-size: 14px; margin: 0;">※ 현재는 정비구역 지정 단계입니다. 실제 입주까지는 최소 5년 이상 소요됩니다.</p>')
    html.append('</div>')
    
    html.append('<hr style="border-top: 2px solid #e0e0e0; margin: 40px 0;">')
    
    # 원문
    html.append('<h2 style="color: #2c3e50; font-size: 20px; margin-top: 40px;">원문 보기</h2>')
    html.append(f'<p><a href="{url}" target="_blank" style="color: #0066cc; text-decoration: underline; font-weight: bold;">부산시 고시공고 원문</a></p>')
    
    html.append('<div style="background: #f8f9fa; padding: 15px; border-radius: 5px; margin-top: 20px;">')
    html.append('<p style="color: #666; font-size: 13px; margin: 0;">')
    html.append(f'※ {datetime.now().strftime("%Y년 %m월 %d일")} 기준<br>')
    html.append('※ 투자 판단은 전문가 상담 후 결정하세요')
    html.append('</p></div>')
    
    return '\n'.join(html)

# ====== 메인 처리 ======
def run_once(driver, detail_url, writer):
    print(f"\n{'='*80}")
    print(f"처리: {detail_url}")
    
    data = extract_detail(driver, detail_url)
    title = data["title"]
    files = data["attachments"]
    
    print(f"\n제목: {title}")
    
    if not files:
        print("❌ 첨부파일 없음")
        if writer:
            writer.writerow([detail_url, title, "(no-file)"])
        return
    
    # 다운로드
    pdfs = download_pdf(driver, files, detail_url, title)
    if not pdfs:
        if writer:
            writer.writerow([detail_url, title, "(download-fail)"])
        return
    
    pdf_path = pdfs[0]
    
    # PDF → 이미지
    print("\n  📄 PDF → 이미지")
    pdf_images = pdf_to_images(pdf_path, title)
    
    # OCR
    print("\n  🔍 OCR")
    text, meta = ocr_pdf(pdf_path)
    
    if meta.get("chars", 0) < OCR_MIN_CHARS:
        print(f"    ❌ OCR 품질 미달")
        if writer:
            writer.writerow([detail_url, title, "(ocr-low)"])
        return
    
    print(f"    ✅ {meta.get('chars')}자")
    
    # 텍스트 저장
    txt_dir = Path(OUT_DIR) / "txt"
    prefix = datetime.now().strftime("%Y%m%d")
    txt_name = f"{prefix}_{clean_filename(title)}.txt"
    with open(txt_dir / txt_name, "w", encoding="utf-8") as f:
        f.write(text)
    
    # 분석
    print("\n  📊 분석")
    info = analyze_text(text, title)
    print(f"    유형: {info.get('type')}")
    print(f"    위치: {info.get('위치', '(미추출)')}")
    
    # 지도
    map_url = ""
    map_images = []
    addr = info.get("위치", "")
    if addr:
        map_url, map_images = capture_naver_map(addr, title)
    
    # 네이버 POI
    print("\n  🏪 주변 시설 조사 (네이버 API)")
    pois = None
    if addr:
        print(f"    대상 주소: {addr}")
        pois = collect_pois_naver(addr)
        
        if pois:
            print(f"    ✅ POI 수집 성공: {len(pois)}개 카테고리")
        else:
            print(f"    ⚠️ POI 수집 실패 또는 결과 없음")
    else:
        print(f"    ⚠️ 주소 추출 실패 - POI 수집 불가")
    
    # HTML
    html_dir = Path(OUT_DIR) / "blog_html"
    html = build_html_with_poi(title, detail_url, info, pdf_images, map_url, map_images, pois)
    
    html_name = f"{prefix}_{clean_filename(title)}.html"
    with open(html_dir / html_name, "w", encoding="utf-8") as f:
        f.write(html)
    
    print(f"\n  ✅ HTML: {html_name}")
    
    # 클립보드
    if pyperclip:
        try:
            pyperclip.copy(html)
            print("  ✅ 클립보드 복사!")
        except:
            pass
    
    if writer:
        writer.writerow([detail_url, title, html_name])
    
    print(f"{'='*80}\n")

def main():
    ensure_dirs()
    
    print("\n" + "="*80)
    print("부산 고시공고 → 블로그 자동화 (네이버 API 버전)")
    print("="*80)
    
    driver = make_driver(headless=HEADLESS_LIST)
    
    try:
        urls = collect_posts(driver)
        
        if not urls:
            print("\n❌ 매칭 없음")
            return
        
        csv_path = Path(CSV_PATH)
        try:
            fp = open(csv_path, "w", newline="", encoding="utf-8-sig")
        except:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_path = csv_path.with_name(f"{csv_path.stem}_{ts}{csv_path.suffix}")
            fp = open(csv_path, "w", newline="", encoding="utf-8-sig")
        
        with fp:
            writer = csv.writer(fp)
            writer.writerow(["url", "title", "html"])
            
            for idx, url in enumerate(urls, 1):
                print(f"\n[{idx}/{len(urls)}]")
                run_once(driver, url, writer)
                time.sleep(1)
        
        print("\n" + "="*80)
        print("✅ 완료!")
        print(f"\n📂 출력:")
        print(f"   HTML: {Path(OUT_DIR) / 'blog_html'}")
        print(f"   PDF 이미지: {Path(OUT_DIR) / 'pdf_images'}")
        print(f"   지도: {Path(OUT_DIR) / 'maps'}")
        print("="*80)
    
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
