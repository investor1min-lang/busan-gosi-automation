# -*- coding: utf-8 -*-
"""
부산 고시공고 크롤러 (GitHub Actions 최적화 버전)
"""

import os
import re
import time
import requests
from pathlib import Path
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ====== CONFIG ======
BASE_URL = "https://www.busan.go.kr/news/gosiboard?articlNo=2"
START_PAGE = 1
END_PAGE = 1
KEYWORDS = ["재개발", "재건축"]

# GitHub Actions 환경에 맞게 수정
OUT_DIR = os.path.join(os.getcwd(), "downloaded_files")
HEADLESS_LIST = True
PAGE_SLEEP = 0.8
TIMEOUT = 15

# Tesseract는 GitHub Actions에서 시스템에 설치되므로 경로 불필요
# PyMuPDF는 pip install로 설치
OCR_MIN_CHARS = 300

# ====== 유틸 함수 ======
def ensure_dirs():
    """필요한 디렉토리 생성"""
    for p in [OUT_DIR, 
              os.path.join(OUT_DIR, "txt"), 
              os.path.join(OUT_DIR, "blog_html"),
              os.path.join(OUT_DIR, "pdf_images"), 
              os.path.join(OUT_DIR, "maps"),
              os.path.join(OUT_DIR, "gosi_html"),
              os.path.join(OUT_DIR, "screenshots")]:
        Path(p).mkdir(parents=True, exist_ok=True)

def make_driver(headless=True):
    """Chrome WebDriver 생성"""
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1400,1100")
    return webdriver.Chrome(options=opts)

def safe_text(el):
    """안전하게 텍스트 추출"""
    try:
        return el.text.strip()
    except:
        return ""

def clean_filename(name):
    """파일명 정리"""
    name = re.sub(r"\s*\(용량[^)]*\)\s*$", "", name or "").strip()
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return (name or "unnamed")[:180]

def normalize_text(text):
    """텍스트 정규화"""
    t = (text or "").lower()
    t = re.sub(r"\s+", " ", t)
    return t.strip()

# ====== 1. 목록 수집 ======
def collect_posts(driver):
    """고시공고 목록 수집"""
    urls = []
    seen_datano = set()
    
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
        
        for row in rows:
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
                
                if any(kw in norm for kw in KEYWORDS):
                    href = title_link.get_attribute("href")
                    
                    if href:
                        # dataNo 기반 중복 체크
                        datano_match = re.search(r'dataNo=(\d+)', href)
                        if datano_match:
                            datano = datano_match.group(1)
                            
                            if datano not in seen_datano:
                                seen_datano.add(datano)
                                urls.append(href)
                                print(f"  ✅ {title[:60]}")
            except:
                continue
    
    print(f"\n📌 총 {len(urls)}개")
    return urls

# ====== 2. 상세 추출 ======
def extract_detail(driver, url):
    """공고 상세 정보 추출"""
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

# ====== 3. PDF 다운로드 ======
def download_pdf(driver, files, referer, title):
    """PDF 파일 다운로드"""
    saved = []
    cookies = requests.cookies.RequestsCookieJar()
    for c in driver.get_cookies():
        cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
    
    for idx, f in enumerate(files, 1):
        try:
            resp = requests.get(f["url"], cookies=cookies, headers={"Referer": referer}, timeout=30, stream=True)
            resp.raise_for_status()
            
            prefix = datetime.now().strftime("%Y%m%d")
            filename = f"{prefix}_{clean_filename(title)[:50]}_{idx}.pdf"
            path = os.path.join(OUT_DIR, filename)
            
            with open(path, "wb") as fp:
                for chunk in resp.iter_content(8192):
                    fp.write(chunk)
            
            saved.append(path)
            print(f"    ✅ {Path(path).name}")
        except Exception as e:
            print(f"    ✗ 다운로드 실패: {e}")
    
    return saved

# ====== 4. PDF → 이미지 변환 ======
def pdf_to_images(pdf_path, title):
    """
    PyMuPDF(fitz)로 PDF → 이미지 변환
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
            # 200 DPI로 렌더링
            mat = fitz.Matrix(200/72, 200/72)
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
    PyMuPDF로 PDF 이미지 추출 후 Tesseract OCR
    """
    try:
        import fitz  # PyMuPDF
        import pytesseract
        from PIL import Image
        import io
    except ImportError as e:
        print(f"    ⚠️ 모듈 누락: {e}")
        return "", {}
    
    try:
        # GitHub Actions에서는 tesseract가 시스템에 설치되어 있음
        # 경로 지정 불필요 (자동 탐지)
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

# ====== 6. 텍스트 분석 ======
def analyze_text(text, title):
    """텍스트에서 정보 추출"""
    info = {"type": "재건축" if "재건축" in title else "재개발"}
    
    # OCR 텍스트 전처리
    text_clean = re.sub(r'\s+', ' ', text)
    
    # 위치 추출 - 다양한 패턴
    found_addr = None
    
    # 패턴 1: "위치" 키워드 이후
    loc_after_label = re.search(r'위\s*치[:\s]*(.{5,80})', text_clean)
    if loc_after_label:
        after_text = loc_after_label.group(1)
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
    
    # 패턴 2: 전체 텍스트에서 직접 찾기
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
    
    # 패턴 3: 제목에서 동 추출 + 본문에서 구/번지 조합
    if not found_addr:
        title_dong = re.search(r'([가-힣]+동)', title)
        if title_dong:
            dong = title_dong.group(1)
            
            # 본문에서 구 찾기
            gu_match = re.search(r'부산(?:광역시)?\s*([가-힣]+구)', text_clean)
            gu = gu_match.group(1) if gu_match else None
            
            # 본문에서 번지 찾기
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

# ====== 테스트 실행 ======
if __name__ == "__main__":
    print("="*80)
    print("부산 고시공고 크롤러 테스트")
    print("="*80)
    
    ensure_dirs()
    
    driver = make_driver(headless=HEADLESS_LIST)
    
    try:
        urls = collect_posts(driver)
        
        if urls:
            print(f"\n첫 번째 공고 테스트:")
            detail = extract_detail(driver, urls[0])
            print(f"제목: {detail['title']}")
            print(f"첨부: {len(detail['attachments'])}개")
    finally:
        driver.quit()
    
    print("\n✅ 테스트 완료")
