# -*- coding: utf-8 -*-
"""
gosi_to_stage.py
부산시청 고시문 수집 → 단계 자동 갱신 + 폴리곤 추출

파이프라인:
  1. busan_blog_최종__1_.py 로 고시문 수집 (제목 + PDF)
  2. 고시 제목 파싱 → 구역명 + 단계 추출
  3. Supabase projects 테이블 stage 갱신 + stage_changes 기록
  4. 구역지정/변경 고시면 → PDF → 이미지 → img_to_zone.py 폴리곤 추출

Usage:
  python gosi_to_stage.py              # 최근 1페이지 확인
  python gosi_to_stage.py --pages 3   # 3페이지까지
  python gosi_to_stage.py --dry-run   # Supabase 반영 없이 확인만
"""

import os
import re
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

# ── Supabase ──────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://winlesksavenrohjymzl.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# ── 상태 파일 (중복 처리 방지) ──────────────────────────────
STATE_FILE = Path(__file__).parent / "gosi_stage_state.json"


# ══════════════════════════════════════════════════════════
# 1. 단계 파싱
# ══════════════════════════════════════════════════════════

# 고시 제목 키워드 → stage 값 (우선순위 순)
STAGE_PATTERNS = [
    (r"준공|사용검사|완공",                         "준공완료"),
    (r"착공",                                        "착공"),
    (r"관리처분계획.*인가|관리처분.*인가",            "관리처분"),
    (r"사업시행.*인가|시행인가",                      "시행인가"),
    (r"조합설립.*인가|조합.*설립인가",               "조합설립"),
    (r"추진위원회.*승인|추진위.*구성",               "추진위원회구성"),
    (r"정비구역.*지정|구역.*지정|구역.*변경|정비계획.*결정", "구역지정"),
]

# 구역명 추출 패턴 (고시 제목에서)
ZONE_NAME_PATTERNS = [
    # "광안5구역", "사직4 재개발", "남천2 재건축"
    r"([가-힣]+\d+(?:-\d+)?)\s*(?:구역|재개발|재건축)",
    # "제X호 광안5구역"
    r"제\S+호[^,]*?([가-힣]+\d+(?:-\d+)?)\s*(?:구역|재개발|재건축)",
    # 괄호 안 구역명
    r"[（(]([가-힣]+\d+(?:-\d+)?(?:구역)?)[）)]",
]


def parse_stage(title: str) -> str | None:
    """고시 제목 → stage 값"""
    for pattern, stage in STAGE_PATTERNS:
        if re.search(pattern, title):
            return stage
    return None


def parse_zone_names(title: str) -> list[str]:
    """고시 제목 → 구역명 목록 (복수 구역 고시 대응)"""
    names = []
    for pattern in ZONE_NAME_PATTERNS:
        for m in re.finditer(pattern, title):
            name = m.group(1).strip()
            # "재개발", "재건축" 접미 제거
            name = re.sub(r"\s*(재개발|재건축|정비구역|구역)$", "", name).strip()
            if name and name not in names:
                names.append(name)
    return names


def is_zone_designation(title: str) -> bool:
    """구역지정/변경 고시 여부 (→ 폴리곤 추출 대상)"""
    return bool(re.search(r"정비구역.*지정|구역.*지정|구역.*변경|정비계획.*결정", title))


# ══════════════════════════════════════════════════════════
# 2. Supabase 갱신
# ══════════════════════════════════════════════════════════

def get_supabase():
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SERVICE_KEY 환경변수 없음")
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def update_stage(client, zone_name: str, new_stage: str, gosi_title: str, dry_run: bool) -> bool:
    """projects 테이블 stage 갱신 + stage_changes 기록"""
    # 현재 stage 조회
    res = client.table("projects").select("id,stage,project_type").eq("name", zone_name).execute()
    if not res.data:
        print(f"    ⚠️  DB에 없음: {zone_name}")
        return False

    row = res.data[0]
    old_stage = row["stage"]

    if old_stage == new_stage:
        print(f"    ↔  변경 없음: {zone_name} ({new_stage})")
        return False

    print(f"    ✅ {zone_name}: {old_stage} → {new_stage}")

    if dry_run:
        return True

    # projects 갱신
    client.table("projects").update({
        "stage": new_stage,
        "updated_at": datetime.now().isoformat(),
    }).eq("id", row["id"]).execute()

    # stage_changes 기록
    client.table("stage_changes").insert({
        "name": zone_name,
        "project_type": row["project_type"],
        "old_stage": old_stage,
        "new_stage": new_stage,
        "changed_at": datetime.now().isoformat(),
        "source": "gosi_auto",
        "gosi_title": gosi_title[:200],
    }).execute()

    return True


# ══════════════════════════════════════════════════════════
# 3. 폴리곤 추출 (구역지정 고시)
# ══════════════════════════════════════════════════════════

def extract_polygon_from_pdf(pdf_path: str, zone_name: str, dong: str = None, dry_run: bool = False):
    """PDF → 이미지 → img_to_zone 경계 추출 → Supabase 저장"""
    try:
        import fitz
    except ImportError:
        print("    ⚠️  PyMuPDF 미설치 (pip install PyMuPDF)")
        return

    import cv2, numpy as np
    from img_to_zone import detect_boundary, extract_largest_contour, simple_transform
    from shapely.geometry import Polygon, mapping

    doc = fitz.open(pdf_path)
    print(f"    PDF {len(doc)}페이지")

    best_result = None  # (zone_name, coords, page_num)

    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        mat = fitz.Matrix(200 / 72, 200 / 72)  # 200 DPI
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")

        arr = np.frombuffer(img_bytes, dtype=np.uint8)
        img_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img_bgr is None:
            continue

        # 색상 자동 감지
        mask = detect_boundary(img_bgr, "auto")
        px_polygon = extract_largest_contour(mask, nth=0)

        if px_polygon is None or len(px_polygon) < 4:
            # black(Canny) 재시도
            mask = detect_boundary(img_bgr, "black")
            px_polygon = extract_largest_contour(mask, nth=1)

        if px_polygon is not None and len(px_polygon) >= 4:
            print(f"    ✅ 페이지{page_num+1} 경계 감지: {len(px_polygon)}점")
            best_result = (page_num + 1, px_polygon, img_bgr.shape)
            break  # 첫 번째 성공 페이지 사용

    doc.close()

    if best_result is None:
        print(f"    ❌ 경계 감지 실패: {zone_name}")
        return

    _, px_polygon, img_shape = best_result

    # 좌표 변환 — dong 있으면 V-World, 없으면 부산 중심 임시값
    if dong:
        from img_to_zone import geocode_jibun
        center = geocode_jibun(dong, "")
    else:
        center = None

    if center:
        center_lng, center_lat = center
    else:
        center_lng, center_lat = 129.075, 35.180  # 부산 중심 (위치 보정 필요 표시)
        print(f"    ⚠️  dong 없음 → 부산 중심 임시 배치 (위치 보정 필요)")

    coords = simple_transform(px_polygon, img_shape, center_lng, center_lat, scale=1200)
    poly = Polygon(coords)
    if not poly.is_valid:
        poly = poly.buffer(0)

    geojson_feature = {
        "type": "Feature",
        "geometry": mapping(poly),
        "properties": {
            "name": zone_name,
            "source": "gosi_auto",
            "needs_position_check": center is None,
        },
    }

    # GeoJSON 로컬 저장
    out_path = Path(__file__).parent / f"{zone_name}.geojson"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": [geojson_feature]}, f,
                  ensure_ascii=False, indent=2)
    print(f"    GeoJSON: {out_path}")

    if dry_run:
        return

    # Supabase 저장
    if SUPABASE_KEY:
        client = get_supabase()
        client.table("zone_boundaries").upsert({
            "project_name": zone_name,
            "geojson": geojson_feature,
            "source": "gosi_auto",
            "needs_position_check": center is None,
        }, on_conflict="project_name").execute()
        print(f"    Supabase 저장 완료")


# ══════════════════════════════════════════════════════════
# 4. 상태 관리 (중복 처리 방지)
# ══════════════════════════════════════════════════════════

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"processed_urls": []}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ══════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=1, help="크롤할 페이지 수 (기본 1)")
    parser.add_argument("--dry-run", action="store_true", help="Supabase 반영 없이 출력만")
    args = parser.parse_args()

    dry_run = args.dry_run
    if dry_run:
        print("=== DRY RUN 모드 (DB 반영 없음) ===\n")

    # 고시문 수집 모듈 임포트
    sys.path.insert(0, str(Path(__file__).parent / "송미승"))
    try:
        from busan_blog_최종__1_ import (
            make_driver, collect_posts, extract_detail,
            download_pdf, pdf_to_images, HEADLESS_LIST, OUT_DIR,
        )
    except ImportError as e:
        print(f"❌ 수집 모듈 임포트 실패: {e}")
        sys.exit(1)

    # 페이지 수 조정
    import busan_blog_최종__1_ as crawler_mod
    crawler_mod.START_PAGE = 1
    crawler_mod.END_PAGE = args.pages

    state = load_state()
    processed = set(state["processed_urls"])

    client = None
    if not dry_run and SUPABASE_KEY:
        client = get_supabase()

    driver = make_driver(headless=HEADLESS_LIST)

    try:
        urls = collect_posts(driver)
        new_urls = [u for u in urls if u not in processed]
        print(f"\n신규 고시: {len(new_urls)}건 / 전체: {len(urls)}건\n")

        for url in new_urls:
            detail = extract_detail(driver, url)
            title = detail["title"]
            print(f"\n[고시] {title}")

            # 단계 파싱
            stage = parse_stage(title)
            zone_names = parse_zone_names(title)

            print(f"  단계: {stage or '(파싱 불가)'}")
            print(f"  구역명: {zone_names or '(파싱 불가)'}")

            # 단계 갱신
            if stage and zone_names and client:
                for zn in zone_names:
                    update_stage(client, zn, stage, title, dry_run)

            # 구역지정 → 폴리곤 추출
            if is_zone_designation(title) and detail["attachments"]:
                print(f"  → 구역지정 고시: 폴리곤 추출 시도")
                pdf_paths = download_pdf(driver, detail["attachments"], url, title)
                for pdf_path in pdf_paths:
                    # 구역명으로 dong 추정 (없으면 None)
                    dong = None
                    if zone_names:
                        dong = f"부산광역시 {zone_names[0]}"  # 개선 여지 있음
                    for zn in (zone_names or ["unknown"]):
                        extract_polygon_from_pdf(pdf_path, zn, dong=dong, dry_run=dry_run)

            # 처리 완료 기록
            processed.add(url)

        state["processed_urls"] = list(processed)
        save_state(state)

    finally:
        driver.quit()

    print("\n완료.")


if __name__ == "__main__":
    main()
