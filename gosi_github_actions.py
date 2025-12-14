# -*- coding: utf-8 -*-
"""
부산 고시공고 자동 알림 (GitHub Actions)
- HTML 생성 제거
- Selenium 제거
- PDF 이미지 직접 전송
"""

import os
import sys
import json
import base64
import requests
from pathlib import Path
from datetime import datetime

# busan_blog 모듈의 함수들 임포트
sys.path.append(str(Path(__file__).parent))
from busan_blog_최종__1_ import (
    collect_posts,
    make_driver,
    extract_detail,
    download_pdf,
    pdf_to_images,
    ocr_pdf,
    analyze_text,
    HEADLESS_LIST,
    OUT_DIR
)

# ====== 설정 ======
STATE_FILE = "gosi_state.json"
LOG_FILE = "gosi_auto.log"

# 환경 변수에서 읽기 (GitHub Secrets)
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
KAKAO_ACCESS_TOKEN = os.getenv("KAKAO_ACCESS_TOKEN")
KAKAO_REFRESH_TOKEN = os.getenv("KAKAO_REFRESH_TOKEN")
IMGBB_API_KEY = os.getenv("IMGBB_API_KEY")


# ====== 로그 함수 ======
def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] {message}"
    print(log_msg)
    
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_msg + '\n')
    except:
        pass


# ====== 상태 관리 ======
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {"processed": []}
    return {"processed": []}


def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ====== 카카오톡 토큰 갱신 ======
def refresh_kakao_token():
    """카카오 액세스 토큰 갱신"""
    if not KAKAO_REST_API_KEY or not KAKAO_REFRESH_TOKEN:
        log("❌ REST API 키 또는 리프레시 토큰 없음")
        return None
    
    token_url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": KAKAO_REST_API_KEY,
        "refresh_token": KAKAO_REFRESH_TOKEN
    }
    
    try:
        response = requests.post(token_url, data=data)
        response.raise_for_status()
        tokens = response.json()
        
        new_access_token = tokens["access_token"]
        log("✅ 카카오 토큰 갱신 성공")
        
        # GitHub Actions에서는 환경변수 업데이트 불가
        # 다음 실행 시 자동 갱신됨
        return new_access_token
        
    except Exception as e:
        log(f"❌ 토큰 갱신 실패: {e}")
        return None


# ====== imgbb 이미지 업로드 ======
def upload_to_imgbb(image_path):
    """이미지를 imgbb에 업로드하고 URL 반환"""
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


# ====== 카카오톡 전송 ======
def send_kakao_message(post_data, info, image_urls):
    """카카오톡으로 이미지 포함 메시지 전송"""
    access_token = KAKAO_ACCESS_TOKEN
    
    if not access_token:
        log("❌ 카카오 액세스 토큰 없음")
        return False
    
    title = post_data['title']
    location = info.get('위치', '부산')
    project_type = info.get('type', '재개발')
    url = post_data['url']
    date_str = datetime.now().strftime("%Y년 %m월 %d일")
    
    log(f"🔗 공고 URL: {url}")
    
    if not url or not url.startswith('http'):
        log(f"⚠️ 잘못된 URL 감지: {url}")
        url = "https://www.busan.go.kr/news/gosiboard"
    
    # API 설정
    api_url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"
    }
    
    # 메시지 1: 대표 이미지 + 기본 정보
    if image_urls:
        message_text = f"""🚨 새 고시공고 발견!

📋 {title}
📍 {location}
🏗️ {project_type}
📅 {date_str}

📸 카드뉴스 1/{len(image_urls)}:
{image_urls[0]}

🔗 부산시청 원문:
{url}

💡 @Chok.sense1 부산 재개발 신속 알림"""
    else:
        message_text = f"""🚨 새 고시공고 발견!

📋 {title}
📍 {location}
🏗️ {project_type}
📅 {date_str}

🔗 상세보기:
{url}

💡 @Chok.sense1 부산 재개발 신속 알림"""
    
    template_object = {
        "object_type": "text",
        "text": message_text,
        "link": {
            "web_url": url,
            "mobile_web_url": url
        }
    }
    
    data = {
        "template_object": json.dumps(template_object, ensure_ascii=False)
    }
    
    try:
        # 메시지 전송
        response = requests.post(api_url, headers=headers, data=data)
        
        # 토큰 만료 시 갱신 후 재시도
        if response.status_code == 401:
            log("🔄 토큰 만료, 갱신 시도...")
            access_token = refresh_kakao_token()
            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"
                response = requests.post(api_url, headers=headers, data=data)
        
        response.raise_for_status()
        result = response.json()
        
        if result.get("result_code") == 0:
            log("✅ 카카오톡 메시지 전송 성공")
            
            # 메시지 2: 나머지 이미지들 (2~5번째)
            if len(image_urls) > 1:
                remaining_images = "\n\n".join([f"📸 {i+2}/{len(image_urls)}:\n{img_url}" 
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
        else:
            log(f"❌ 메시지 전송 실패: {result}")
            return False
            
    except Exception as e:
        log(f"❌ 카톡 전송 오류: {e}")
        return False


# ====== 메인 처리 함수 ======
def process_new_gosi(post_data):
    """새 고시공고 전체 처리"""
    log(f"\n{'='*80}")
    log(f"📝 처리 시작: {post_data['title'][:60]}")
    log(f"{'='*80}\n")
    
    driver = None
    
    try:
        driver = make_driver(headless=True)  # GitHub Actions는 항상 headless
        
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
        
        # 5. 이미지 업로드 (최대 5장)
        log(f"📤 {len(pdf_images[:5])}장 이미지 업로드 중...")
        image_urls = []
        for i, img_path in enumerate(pdf_images[:5], 1):
            img_url = upload_to_imgbb(img_path)
            if img_url:
                image_urls.append(img_url)
                log(f"  [{i}/{min(5, len(pdf_images))}] 업로드 완료")
            else:
                log(f"  [{i}/{min(5, len(pdf_images))}] 업로드 실패")
        
        # 6. 카카오톡 전송
        log("📤 카카오톡 전송 중...")
        kakao_success = send_kakao_message(post_data, info, image_urls)
        
        if kakao_success:
            log("✅ 전체 프로세스 완료!")
            return True
        else:
            log("⚠️ 카톡 전송 실패")
            return False
        
    except Exception as e:
        log(f"❌ 처리 실패: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass


# ====== 메인 실행 ======
def main():
    log("\n" + "="*80)
    log("🚀 부산 고시공고 자동화 시스템 시작 (GitHub Actions)")
    log("="*80)
    
    # 환경 변수 확인
    if not all([KAKAO_REST_API_KEY, KAKAO_ACCESS_TOKEN, KAKAO_REFRESH_TOKEN, IMGBB_API_KEY]):
        log("❌ 환경 변수 설정 확인 필요!")
        log(f"  KAKAO_REST_API_KEY: {'✅' if KAKAO_REST_API_KEY else '❌'}")
        log(f"  KAKAO_ACCESS_TOKEN: {'✅' if KAKAO_ACCESS_TOKEN else '❌'}")
        log(f"  KAKAO_REFRESH_TOKEN: {'✅' if KAKAO_REFRESH_TOKEN else '❌'}")
        log(f"  IMGBB_API_KEY: {'✅' if IMGBB_API_KEY else '❌'}")
        return
    
    # 상태 파일 로드
    state = load_state()
    processed_ids = set(state.get("processed", []))
    
    log("\n" + "="*80)
    log(f"🔍 새 공고 확인 중...")
    log("="*80)
    
    # 공고 수집
    posts = collect_posts()
    
    if not posts:
        log("📌 공고 없음")
        return
    
    log(f"📌 총 {len(posts)}개 공고 발견")
    
    # 새 공고 필터링
    new_posts = []
    driver = None
    
    try:
        driver = make_driver(headless=True)
        
        for post_url in posts:
            detail = extract_detail(driver, post_url)
            post_id = post_url.split("dataNo=")[1].split("&")[0] if "dataNo=" in post_url else post_url
            
            if post_id not in processed_ids:
                detail['url'] = post_url
                detail['id'] = post_id
                new_posts.append(detail)
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass
    
    if not new_posts:
        log("✅ 모든 공고 이미 처리됨")
        return
    
    log(f"🆕 미처리 공고 {len(new_posts)}개 발견!")
    
    # 새 공고 처리
    for idx, post_data in enumerate(new_posts, 1):
        log(f"\n[{idx}/{len(new_posts)}] {post_data['url']}")
        log(f"제목: {post_data['title']}")
        log(f"첨부: {len(post_data['attachments'])}개\n")
        
        success = process_new_gosi(post_data)
        
        if success:
            processed_ids.add(post_data['id'])
            state["processed"] = list(processed_ids)
            save_state(state)
        
        # 여러 공고 처리 시 대기
        if idx < len(new_posts):
            import time
            time.sleep(2)
    
    log("\n" + "="*80)
    log("✅ 전체 처리 완료")
    log("="*80)
    log("\n✅ 프로그램 종료")


if __name__ == "__main__":
    main()
