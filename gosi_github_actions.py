# -*- coding: utf-8 -*-
"""
부산 고시공고 자동화 (GitHub Actions용)
- PDF 다운로드 → 이미지 변환
- HTML 자동 생성 + 스크린샷
- 카카오톡 전송
"""

import os
import sys
import json
import base64
import requests
from datetime import datetime
from pathlib import Path
from PIL import Image
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
import time

# 기존 크롤러 모듈 임포트
try:
    from busan_blog import (
        make_driver, collect_posts, extract_detail,
        download_pdf, pdf_to_images, ocr_pdf,
        analyze_text, ensure_dirs, HEADLESS_LIST, OUT_DIR
    )
except ImportError as e:
    print(f"❌ 크롤러 모듈 임포트 실패: {e}")
    sys.exit(1)

# ====== 설정 ======
STATE_FILE = "gosi_state.json"
HTML_TEMPLATE = "redevelopment_final_v4.html"
KAKAO_TOKEN_FILE = "kakao_token.json"
LOG_FILE = "gosi_auto.log"
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY", "")

# ====== 로그 함수 ======
def log(message):
    """로그 출력 및 저장"""
    timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    log_msg = f"{timestamp} {message}"
    print(log_msg)
    
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_msg + '\n')
    except:
        pass


# ====== 상태 관리 ======
def load_state():
    """이미 처리한 공고 목록 로드"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {"processed": []}
    return {"processed": []}


def save_state(state):
    """상태 저장"""
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def is_processed(url):
    """이미 처리된 공고인지 확인"""
    state = load_state()
    return url in state.get("processed", [])


def mark_processed(url):
    """처리 완료로 표시"""
    state = load_state()
    if url not in state.get("processed", []):
        state["processed"].append(url)
        save_state(state)


# ====== 카카오톡 토큰 관리 ======
def load_kakao_token():
    """카카오 토큰 로드"""
    if not os.path.exists(KAKAO_TOKEN_FILE):
        log(f"❌ 카카오 토큰 파일 없음: {KAKAO_TOKEN_FILE}")
        return None
    
    with open(KAKAO_TOKEN_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def refresh_kakao_token():
    """카카오 액세스 토큰 갱신"""
    token_data = load_kakao_token()
    if not token_data:
        return None
    
    api_key = token_data.get("rest_api_key")
    refresh_token = token_data.get("refresh_token")
    
    if not api_key or not refresh_token:
        log("❌ REST API 키 또는 리프레시 토큰 없음")
        return None
    
    token_url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": api_key,
        "refresh_token": refresh_token
    }
    
    try:
        log("🔄 토큰 만료, 갱신 시도...")
        response = requests.post(token_url, data=data)
        response.raise_for_status()
        
        tokens = response.json()
        
        # 기존 데이터 업데이트
        token_data["access_token"] = tokens["access_token"]
        token_data["expires_in"] = tokens["expires_in"]
        
        # 새 리프레시 토큰이 있으면 업데이트
        if "refresh_token" in tokens:
            token_data["refresh_token"] = tokens["refresh_token"]
        
        with open(KAKAO_TOKEN_FILE, 'w', encoding='utf-8') as f:
            json.dump(token_data, f, ensure_ascii=False, indent=2)
        
        log("✅ 카카오 토큰 갱신 성공")
        return token_data["access_token"]
        
    except Exception as e:
        log(f"❌ 토큰 갱신 실패: {e}")
        return None


# ====== imgbb 이미지 업로드 ======
def upload_to_imgbb(image_path):
    """
    이미지를 imgbb에 업로드하고 URL 반환
    """
    try:
        with open(image_path, 'rb') as f:
            img_data = base64.b64encode(f.read()).decode('utf-8')
        
        url = "https://api.imgbb.com/1/upload"
        payload = {
            "key": IMGBB_API_KEY,
            "image": img_data
        }
        
        response = requests.post(url, data=payload, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        
        if result.get("success"):
            image_url = result["data"]["url"]
            log(f"  ✅ 이미지 업로드: {Path(image_path).name}")
            return image_url
        else:
            log(f"  ❌ 업로드 실패: {result}")
            return None
            
    except Exception as e:
        log(f"  ❌ imgbb 업로드 오류: {e}")
        return None


# ====== HTML 생성 (이미지 자동 삽입) ======
def create_html_with_images(post_data, info, image_paths):
    """
    HTML 생성 - 제목과 이미지를 자동으로 삽입
    """
    if not os.path.exists(HTML_TEMPLATE):
        log(f"❌ HTML 템플릿 없음: {HTML_TEMPLATE}")
        return None
    
    with open(HTML_TEMPLATE, 'r', encoding='utf-8') as f:
        html_template = f.read()
    
    # 기본 정보
    title = post_data['title']
    location = info.get('위치', '부산')
    
    # 날짜 포맷팅 - "2025년 12월 3일" 형식
    today = datetime.now()
    date_kr = f"{today.year}년 {today.month}월 {today.day}일"
    date_iso = today.strftime("%Y-%m-%d")
    
    project_type = info.get('type', '재개발')
    
    # 이미지들을 base64로 변환
    images_base64 = []
    for img_path in image_paths[:10]:  # 최대 10장
        try:
            with open(img_path, 'rb') as f:
                img_data = base64.b64encode(f.read()).decode('utf-8')
                images_base64.append(f"data:image/png;base64,{img_data}")
        except Exception as e:
            log(f"⚠️ 이미지 변환 실패 {img_path}: {e}")
    
    if not images_base64:
        log("❌ 변환된 이미지 없음")
        return None
    
    # JavaScript 코드 - 기본 정보 + 이미지 자동 삽입
    js_code = f"""
    <script>
    window.addEventListener('DOMContentLoaded', function() {{
        // 기본 정보 입력
        const locationInput = document.getElementById('locationInput');
        if (locationInput) locationInput.value = {json.dumps(location)};
        
        const projectInput = document.getElementById('projectInput');
        if (projectInput) projectInput.value = {json.dumps(title)};
        
        const dateInput = document.getElementById('dateInput');
        if (dateInput) dateInput.value = {json.dumps(date_kr)};
        
        const typeInput = document.getElementById('typeInput');
        if (typeInput) typeInput.value = {json.dumps(project_type)};
        
        // Display 영역 업데이트
        const displayLocation = document.getElementById('displayLocation');
        if (displayLocation) displayLocation.textContent = {json.dumps(location)};
        
        const displayProject = document.getElementById('displayProject');
        if (displayProject) displayProject.textContent = {json.dumps(title)};
        
        const displayDate = document.getElementById('displayDate');
        if (displayDate) displayDate.textContent = {json.dumps(date_kr)};
        
        const displayType = document.getElementById('displayType');
        if (displayType) displayType.textContent = {json.dumps(project_type)};
        
        // 이미지 자동 삽입
        const images = {json.dumps(images_base64)};
        
        images.forEach((imgData, index) => {{
            const pageNum = index + 1;
            const pageItem = document.querySelector(`#page${{pageNum}}`);
            
            if (!pageItem && pageNum > 1) {{
                // 2페이지 이상이면 페이지 추가
                if (typeof addPage === 'function') {{
                    addPage();
                }}
            }}
            
            // 다시 페이지 아이템 찾기
            const actualPageItem = document.querySelector(`#page${{pageNum}}`);
            if (!actualPageItem) return;
            
            const img = actualPageItem.querySelector('.notice-image');
            const uploadArea = actualPageItem.querySelector('.image-upload-area');
            const canvasWrapper = actualPageItem.querySelector('.canvas-wrapper');
            
            if (img && uploadArea && canvasWrapper) {{
                img.src = imgData;
                img.onload = function() {{
                    uploadArea.style.display = 'none';
                    canvasWrapper.classList.add('active');
                    actualPageItem.classList.add('has-image');
                }};
                
                // markingStates 초기화
                if (typeof markingStates !== 'undefined' && !markingStates[pageNum]) {{
                    markingStates[pageNum] = {{
                        originalImage: imgData,
                        tool: 'select',
                        color: '#ffff00',
                        thickness: 'normal',
                        drawings: []
                    }};
                }}
            }}
        }});
        
        // 페이지 카운트 업데이트
        const pageCountEl = document.getElementById('pageCount');
        if (pageCountEl) pageCountEl.textContent = images.length;
        
        // 각 페이지의 페이지 번호 업데이트
        images.forEach((_, index) => {{
            const pageNum = index + 1;
            const pageNumber = document.querySelector(`#page${{pageNum}} .page-number`);
            if (pageNumber) {{
                pageNumber.textContent = `${{pageNum}} / ${{images.length}}`;
            }}
        }});
        
        console.log('✅ 데이터 자동 입력 완료:', images.length, '페이지');
    }});
    </script>
    """
    
    # HTML에 JavaScript 삽입 (</body> 직전)
    html_with_js = html_template.replace('</body>', js_code + '\n</body>')
    
    return html_with_js


# ====== 스크린샷 촬영 ======
def capture_all_pages(html_path):
    """
    HTML의 모든 페이지를 스크린샷으로 저장
    """
    screenshot_dir = Path(OUT_DIR) / "screenshots"
    screenshot_dir.mkdir(exist_ok=True, parents=True)
    
    # Chrome 옵션
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=900,1600')
    
    driver = webdriver.Chrome(options=options)
    screenshot_paths = []
    
    try:
        # HTML 파일 열기
        file_url = f"file://{html_path.absolute()}"
        driver.get(file_url)
        
        # 페이지 로딩 대기
        time.sleep(3)
        
        # 페이지 수 확인
        try:
            page_count_el = driver.find_element(By.ID, "pageCount")
            total_pages = int(page_count_el.text)
        except:
            total_pages = 1
        
        log(f"📸 총 {total_pages}개 페이지 캡처 중...")
        
        # 각 페이지 캡처
        for page_num in range(1, total_pages + 1):
            try:
                card = driver.find_element(By.CSS_SELECTOR, f"#page{page_num}Wrapper .card")
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                screenshot_name = f"{timestamp}_page{page_num}.png"
                screenshot_path = screenshot_dir / screenshot_name
                
                card.screenshot(str(screenshot_path))
                screenshot_paths.append(screenshot_path)
                log(f"  ✅ 페이지 {page_num} 저장")
                
            except Exception as e:
                log(f"  ⚠️ 페이지 {page_num} 캡처 실패: {e}")
        
        log(f"✅ 총 {len(screenshot_paths)}개 페이지 스크린샷 완료")
        return screenshot_paths
        
    except Exception as e:
        log(f"❌ 스크린샷 오류: {e}")
        return []
        
    finally:
        driver.quit()


# ====== 카카오톡 전송 ======
def send_kakao_message(post_data, info, screenshot_paths):
    """
    카카오톡으로 카드뉴스 전송
    """
    if not screenshot_paths:
        log("❌ 전송할 이미지 없음")
        return False
    
    token_data = load_kakao_token()
    if not token_data:
        return False
    
    access_token = token_data.get("access_token")
    url = post_data['url']
    title = post_data['title']
    
    # imgbb 업로드
    log(f"📤 {len(screenshot_paths)}장 이미지 업로드 중...")
    image_urls = []
    
    for idx, path in enumerate(screenshot_paths[:5], 1):  # 최대 5장
        img_url = upload_to_imgbb(path)
        if img_url:
            image_urls.append(img_url)
            log(f"  [{idx}/{min(len(screenshot_paths), 5)}] 업로드 완료")
    
    if not image_urls:
        log("❌ 이미지 업로드 실패")
        return False
    
    # 카카오톡 전송
    api_url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    try:
        log("📤 카카오톡 전송 중...")
        log(f"🔗 공고 URL: {url}")
        
        # 메시지 1: 대표 이미지 + 기본 정보
        main_text = f"""🏠 새 고시공고 발견!

📍 {info.get('위치', '부산')}
🏗️ {info.get('type', '재개발')}

📋 {title[:100]}

📸 총 {len(screenshot_paths)}페이지
👆 이미지를 탭하면 크게 볼 수 있어요!"""
        
        template_object = {
            "object_type": "feed",
            "content": {
                "title": "부산 재개발·재건축 고시 공고",
                "description": main_text,
                "image_url": image_urls[0],
                "link": {
                    "web_url": url,
                    "mobile_web_url": url
                }
            }
        }
        
        data = {
            "template_object": json.dumps(template_object, ensure_ascii=False)
        }
        
        response = requests.post(api_url, headers=headers, data=data)
        result = response.json()
        
        if result.get("result_code") == 0:
            log("✅ 카카오톡 메시지 전송 성공")
            
            # 메시지 2: 추가 이미지 (2~5장)
            if len(image_urls) > 1:
                remaining_images = "\n\n".join([f"📸 {i+2}/{len(screenshot_paths)}:\n{img_url}" 
                                                for i, img_url in enumerate(image_urls[1:])])
                
                detail_msg = f"""📸 추가 카드뉴스

{remaining_images}

💡 이미지 URL을 클릭하면 크게 볼 수 있어요!"""
                
                template_object2 = {
                    "object_type": "text",
                    "text": detail_msg,
                    "link": {
                        "web_url": url,
                        "mobile_web_url": url
                    }
                }
                
                data2 = {
                    "template_object": json.dumps(template_object2, ensure_ascii=False)
                }
                
                requests.post(api_url, headers=headers, data=data2)
                log(f"✅ 추가 이미지 {len(image_urls)-1}장 전송")
            
            return True
        
        elif result.get("code") == -401:
            # 토큰 만료 시 갱신 후 재시도
            new_token = refresh_kakao_token()
            if new_token:
                headers["Authorization"] = f"Bearer {new_token}"
                response = requests.post(api_url, headers=headers, data=data)
                result = response.json()
                
                if result.get("result_code") == 0:
                    log("✅ 카카오톡 메시지 전송 성공")
                    return True
        
        log(f"❌ 메시지 전송 실패: {result}")
        return False
            
    except Exception as e:
        log(f"❌ 카톡 전송 오류: {e}")
        return False


# ====== 메인 처리 함수 ======
def process_new_gosi(post_data):
    """
    새 고시공고 전체 처리
    """
    log(f"\n{'='*80}")
    log(f"📝 처리 시작: {post_data['title'][:60]}")
    log(f"{'='*80}\n")
    
    driver = None
    
    try:
        # 폴더 생성
        ensure_dirs()
        
        driver = make_driver(headless=HEADLESS_LIST)
        
        url = post_data['url']
        title = post_data['title']
        files = post_data['attachments']
        
        log(f"🔗 원본 URL: {url}")
        
        if not files:
            log("❌ 첨부파일 없음")
            return False
        
        # 1. PDF 다운로드
        log("📥 PDF 다운로드 중...")
        pdfs = download_pdf(driver, files, url, title)
        if not pdfs:
            log("❌ 다운로드 실패")
            return False
        
        pdf_path = pdfs[0]
        log(f"✅ 다운로드 완료: {Path(pdf_path).name}")
        
        # 2. PDF → 이미지
        log("📄 PDF → 이미지 변환 중...")
        pdf_images = pdf_to_images(pdf_path, title)
        log(f"✅ {len(pdf_images)}장 변환 완료")
        
        if not pdf_images:
            log("❌ 이미지 변환 실패")
            return False
        
        # 3. OCR (텍스트 분석용)
        log("🔍 OCR 처리 중...")
        text, meta = ocr_pdf(pdf_path)
        
        # 4. 텍스트 분석
        log("📊 데이터 분석 중...")
        info = analyze_text(text, title)
        log(f"✅ 유형: {info.get('type', '기타')}")
        log(f"✅ 위치: {info.get('위치', '(미추출)')}")
        
        # 5. HTML 생성
        log("📝 HTML 생성 중...")
        html_content = create_html_with_images(post_data, info, pdf_images)
        
        if not html_content:
            log("❌ HTML 생성 실패")
            return False
        
        # 6. HTML 저장
        html_dir = Path(OUT_DIR) / "gosi_html"
        html_dir.mkdir(exist_ok=True, parents=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        html_name = f"{timestamp}_{title[:50]}.html"
        html_name = html_name.replace('/', '_').replace('\\', '_').replace(':', '_')
        
        html_path = html_dir / html_name
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        
        log(f"✅ HTML 저장: {html_path}")
        
        # 7. 스크린샷 (모든 페이지)
        log("📸 카드뉴스 캡처 중...")
        screenshot_paths = capture_all_pages(html_path)
        
        if not screenshot_paths:
            log("❌ 스크린샷 실패")
            return False
        
        # 8. 카카오톡 전송
        log("📤 카카오톡 전송 중...")
        kakao_success = send_kakao_message(post_data, info, screenshot_paths)
        
        if kakao_success:
            log("✅ 전체 프로세스 완료!")
            mark_processed(url)
            return True
        else:
            log("⚠️ 카톡 전송 실패했지만 HTML은 생성됨")
            mark_processed(url)
            return True
        
    except Exception as e:
        log(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass


# ====== 새 공고 체크 ======
def check_new_gosi():
    """
    새 고시공고 확인 및 처리
    """
    log(f"\n{'='*80}")
    log(f"🔍 새 공고 확인 중...")
    log(f"{'='*80}\n")
    
    driver = None
    
    try:
        driver = make_driver(headless=HEADLESS_LIST)
        
        # 공고 목록 수집
        urls = collect_posts(driver)
        
        if not urls:
            log("📭 새 공고 없음")
            return
        
        log(f"📌 총 {len(urls)}개 공고 발견")
        
        # 미처리 공고 필터링
        new_urls = [url for url in urls if not is_processed(url)]
        
        if not new_urls:
            log(f"✅ 모든 공고 이미 처리됨")
            return
        
        log(f"🆕 미처리 공고 {len(new_urls)}개 발견!")
        
        # 각 공고 처리
        for idx, url in enumerate(new_urls, 1):
            log(f"\n[{idx}/{len(new_urls)}] {url}")
            
            try:
                # 상세 정보 추출
                post_data = extract_detail(driver, url)
                
                log(f"제목: {post_data['title'][:80]}")
                log(f"첨부: {len(post_data['attachments'])}개")
                
                if not post_data['attachments']:
                    log("⚠️ 첨부파일 없음 - 건너뛰기")
                    mark_processed(url)
                    continue
                
                # 전체 프로세스 실행
                success = process_new_gosi(post_data)
                
                if not success:
                    log(f"❌ 처리 실패")
                
                # 다음 공고 처리 전 대기
                time.sleep(2)
                
            except Exception as e:
                log(f"❌ 오류: {e}")
                continue
        
        log(f"\n{'='*80}")
        log(f"✅ 전체 처리 완료")
        log(f"{'='*80}\n")
        
    except Exception as e:
        log(f"❌ 모니터링 오류: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass


# ====== 메인 ======
if __name__ == "__main__":
    log("\n" + "="*80)
    log("🚀 부산 고시공고 자동화 시스템 시작 (GitHub Actions)")
    log("="*80)
    
    # 폴더 확인
    ensure_dirs()
    
    # 카카오 토큰 확인
    if not os.path.exists(KAKAO_TOKEN_FILE):
        log(f"⚠️ 카카오 토큰 없음: {KAKAO_TOKEN_FILE}")
        sys.exit(1)
    
    # HTML 템플릿 확인
    if not os.path.exists(HTML_TEMPLATE):
        log(f"⚠️ HTML 템플릿 없음: {HTML_TEMPLATE}")
        sys.exit(1)
    
    # 새 공고 체크 실행
    check_new_gosi()
    
    log("\n✅ 프로그램 종료")
