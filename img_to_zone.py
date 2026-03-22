"""
img_to_zone.py
토지이음 구역경계 이미지(JPG/PNG) → GeoJSON 폴리곤 추출 → 지도 표시

원리:
  1. OpenCV로 이미지에서 빨간 경계선 감지
  2. 경계 폴리곤 윤곽선 추출
  3. OCR로 지번 번호 추출 → V-World 지번 좌표 조회 → GCP(기준점) 생성
  4. 기준점으로 이미지 좌표 → WGS84 어파인 변환
  5. GeoJSON 저장 + Folium 지도 미리보기 + Supabase upsert

사용법:
  python img_to_zone.py --img 구역경계.jpg --name 사직4구역 --scale 1200 --preview

환경변수 (--save 옵션):
  SUPABASE_URL, SUPABASE_SERVICE_KEY, VWORLD_API_KEY
"""

import os
import re
import json
import argparse
import numpy as np
import cv2
from PIL import Image
import pytesseract
import requests
import folium
from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

VWORLD_API_KEY = os.environ.get("VWORLD_API_KEY", "7D47968C-0ADC-334F-86EA-233B5806D2BE")


# ────────────────────────────────────────────
# 1. 경계선 감지 (빨강 / 초록 / 파랑 자동 선택)
# ────────────────────────────────────────────
def detect_boundary(img_bgr: np.ndarray, color: str = "auto") -> np.ndarray:
    """HSV 색공간에서 경계선 마스크 생성. color: auto|red|green|blue|black"""
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)

    def _mask(ranges):
        m = None
        for lo, hi in ranges:
            mm = cv2.inRange(hsv, lo, hi)
            m = mm if m is None else cv2.bitwise_or(m, mm)
        return m

    masks = {
        "red":    _mask([((0,80,80),(10,255,255)), ((165,80,80),(180,255,255))]),
        "green":  _mask([((40,60,60),(90,255,255))]),
        "blue":   _mask([((100,80,80),(130,255,255))]),
        "yellow": _mask([((15,80,80),(35,255,255))]),
    }

    if color == "auto":
        # 픽셀 수가 가장 많은 색 선택
        color = max(("red","green","blue","yellow"), key=lambda c: cv2.countNonZero(masks[c]))
        print(f"  경계선 색상 자동 감지: {color}")

    if color == "black":
        # Canny 엣지 기반 검정 경계선 감지 (점선 연결 위해 팽창+닫기)
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        edges = cv2.Canny(blurred, 30, 100)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        dilated = cv2.dilate(edges, k, iterations=2)
        closed = cv2.morphologyEx(dilated, cv2.MORPH_CLOSE, k, iterations=4)
        return closed

    mask = masks[color]
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    mask = cv2.dilate(mask, kernel, iterations=3)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=4)
    mask = cv2.erode(mask, kernel, iterations=2)
    return mask

# 하위 호환
def detect_red_boundary(img_bgr):
    return detect_boundary(img_bgr, "red")


def extract_largest_contour(mask: np.ndarray, nth: int = 0) -> np.ndarray | None:
    """마스크에서 nth번째로 큰 윤곽선 추출.
    nth=0: 가장 큰 (색상 감지용)
    nth=1: 2번째로 큰 (black 모드: 이미지 테두리 제외 후 경계 추출)
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    sorted_cnts = sorted(contours, key=cv2.contourArea, reverse=True)
    if len(sorted_cnts) <= nth:
        print(f"  윤곽선 {nth+1}위가 없습니다 (총 {len(sorted_cnts)}개)")
        return None
    cnt = sorted_cnts[nth]
    area = cv2.contourArea(cnt)
    print(f"  감지된 윤곽선 면적: {area:.0f} px²  ({len(contours)}개 중 {nth+1}위)")
    if area < 1000:
        return None
    # 폴리곤 단순화
    epsilon = 0.002 * cv2.arcLength(cnt, True)
    approx = cv2.approxPolyDP(cnt, epsilon, True)
    return approx.reshape(-1, 2)


# ────────────────────────────────────────────
# 2. OCR로 지번 추출
# ────────────────────────────────────────────
def ocr_jibun(img_bgr: np.ndarray) -> list[str]:
    """이미지에서 지번 패턴 (숫자-숫자 또는 숫자) 추출"""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    pil_img = Image.fromarray(thresh)
    try:
        text = pytesseract.image_to_string(pil_img, lang="kor+eng",
                                           config="--psm 11")
        # 지번 패턴: 숫자 또는 숫자-숫자 (예: 1458, 1459-5)
        jibun_list = re.findall(r"\b(\d{2,4}(?:-\d{1,3})?)\b", text)
        unique = list(dict.fromkeys(jibun_list))
        return unique[:20]
    except Exception as e:
        print(f"  OCR 오류: {e}")
        return []


# ────────────────────────────────────────────
# 3. V-World로 지번 좌표 조회
# ────────────────────────────────────────────
def geocode_jibun(dong: str, jibun: str) -> tuple | None:
    """V-World 지번 검색 → (lng, lat)"""
    try:
        r = requests.get("https://api.vworld.kr/req/address", params={
            "service": "address", "request": "getcoord",
            "version": "2.0", "crs": "epsg:4326",
            "address": f"{dong} {jibun}",
            "type": "parcel", "format": "json",
            "key": VWORLD_API_KEY,
        }, timeout=10)
        data = r.json()
        if data.get("response", {}).get("status") == "OK":
            pt = data["response"]["result"]["point"]
            return float(pt["x"]), float(pt["y"])
    except Exception:
        pass
    return None


# ────────────────────────────────────────────
# 4. 어파인 변환 계산
# ────────────────────────────────────────────
def compute_transform(gcps: list[tuple]) -> np.ndarray | None:
    """
    GCP: [(img_x, img_y, lng, lat), ...]
    최소 3개 필요. 어파인 변환 행렬 반환.
    """
    if len(gcps) < 3:
        return None
    src = np.float32([[g[0], g[1]] for g in gcps])
    dst = np.float32([[g[2], g[3]] for g in gcps])
    M, _ = cv2.estimateAffinePartial2D(src, dst)
    return M


def apply_transform(px_coords: np.ndarray, M: np.ndarray) -> list[tuple]:
    """이미지 좌표 → WGS84 좌표 변환"""
    n = len(px_coords)
    src = np.float32(px_coords).reshape(n, 1, 2)
    dst = cv2.transform(src, M)
    return [(float(d[0][0]), float(d[0][1])) for d in dst]


# ────────────────────────────────────────────
# 5. 스케일 기반 단순 변환 (GCP 없을 때)
# ────────────────────────────────────────────
def simple_transform(px_coords: np.ndarray, img_shape: tuple,
                     center_lng: float, center_lat: float,
                     scale: int) -> list[tuple]:
    """
    스케일(1:N)과 중심 좌표만으로 근사 변환.
    1px ≈ (scale / 100000) 도 (매우 대략적)
    """
    h, w = img_shape[:2]
    cx_px, cy_px = w / 2, h / 2
    # 1m = 약 0.00001도 (위도 기준)
    # 1px(72dpi) ≈ 0.353mm → 실세계 0.353mm × scale
    dpi = 72
    mm_per_px = 25.4 / dpi
    m_per_px = (mm_per_px / 1000) * scale
    deg_per_px_lat = m_per_px / 111320
    deg_per_px_lng = m_per_px / (111320 * np.cos(np.radians(center_lat)))

    result = []
    for px, py in px_coords:
        lng = center_lng + (px - cx_px) * deg_per_px_lng
        lat = center_lat - (py - cy_px) * deg_per_px_lat
        result.append((lng, lat))
    return result


# ────────────────────────────────────────────
# 6. 지도 미리보기
# ────────────────────────────────────────────
def preview_map(coords_wgs84: list[tuple], name: str, img_path: str,
                img_bgr: np.ndarray, out_html: str):
    center_lat = sum(c[1] for c in coords_wgs84) / len(coords_wgs84)
    center_lng = sum(c[0] for c in coords_wgs84) / len(coords_wgs84)

    m = folium.Map(location=[center_lat, center_lng], zoom_start=16,
                   tiles="CartoDB positron")

    poly = [[lat, lng] for lng, lat in coords_wgs84]
    folium.Polygon(
        locations=poly,
        color="#c0392b", weight=3,
        fill=True, fill_color="#ff6b6b", fill_opacity=0.3,
        tooltip=name,
    ).add_to(m)

    m.save(out_html)
    print(f"  지도 저장: {out_html}")


# ────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--img",    required=True, help="JPG/PNG 파일 경로")
    parser.add_argument("--name",   default=None,  help="사업명")
    parser.add_argument("--dong",   default=None,
                        help="법정동 주소 (예: '부산광역시 동래구 사직동') — GCP 자동 생성용")
    parser.add_argument("--scale",  type=int, default=1200, help="축척 분모 (기본 1200)")
    parser.add_argument("--center", default=None,
                        help="중심 좌표 lng,lat (예: 129.065,35.188)")
    parser.add_argument("--color",  default="auto", choices=["auto","red","green","blue","black","yellow"],
                        help="경계선 색상 (기본: auto). black=흑백 도면 Canny 엣지 감지, yellow=노란 구역 감지")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--save",    action="store_true")
    parser.add_argument("--out",    default=None)
    args = parser.parse_args()

    name = args.name or os.path.splitext(os.path.basename(args.img))[0]
    img_bgr = cv2.imread(args.img)
    if img_bgr is None:
        print(f"이미지 로드 실패: {args.img}")
        return

    h, w = img_bgr.shape[:2]
    print(f"이미지 크기: {w}×{h}px  축척: 1:{args.scale}")

    # 1. 경계 감지
    color = args.color
    mask = detect_boundary(img_bgr, color)
    # black 모드: 가장 큰 윤곽선은 이미지 테두리 → 2위가 경계
    nth = 1 if color == "black" else 0
    px_polygon = extract_largest_contour(mask, nth=nth)
    if px_polygon is None:
        print("경계선 감지 실패")
        return
    print(f"  경계 포인트: {len(px_polygon)}개")

    # 2. 좌표 변환
    if args.center:
        center_lng, center_lat = map(float, args.center.split(","))
    elif args.dong:
        # 법정동 중심 좌표를 V-World로 조회
        res = geocode_jibun(args.dong, "")
        if res:
            center_lng, center_lat = res
            print(f"  중심 좌표: {center_lat:.5f}, {center_lng:.5f}")
        else:
            print("  V-World 중심 좌표 조회 실패")
            return
    else:
        print("--dong 또는 --center 옵션이 필요합니다.")
        return

    # GCP 기반 어파인 변환 시도 (OCR 지번 활용)
    M = None
    if args.dong:
        print("  OCR로 지번 추출 중...")
        jibuns = ocr_jibun(img_bgr)
        print(f"  추출된 지번: {jibuns[:10]}")
        gcps = []
        for jb in jibuns[:15]:
            coord = geocode_jibun(args.dong, jb)
            if coord:
                # 이미지에서 해당 숫자 텍스트 위치 찾기 (근사)
                # 실제로는 pytesseract bounding box 필요 — 여기선 스킵
                pass

    # GCP 실패 시 스케일 기반 단순 변환
    wgs84_coords = simple_transform(px_polygon, img_bgr.shape,
                                    center_lng, center_lat, args.scale)

    # GeoJSON 생성
    poly_shp = Polygon(wgs84_coords)
    if not poly_shp.is_valid:
        poly_shp = poly_shp.buffer(0)

    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": mapping(poly_shp),
            "properties": {"name": name, "scale": args.scale}
        }]
    }

    out_path = args.out or f"{name}.geojson"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)
    print(f"\nGeoJSON 저장: {out_path}")

    # 디버그 이미지 (감지된 경계 표시)
    debug_img = img_bgr.copy()
    cv2.drawContours(debug_img, [px_polygon.reshape(-1, 1, 2)], -1, (0, 255, 0), 3)
    debug_path = f"{name}_debug.jpg"
    cv2.imwrite(debug_path, debug_img)
    print(f"경계 확인 이미지: {debug_path}")

    if args.preview:
        preview_map(wgs84_coords, name, args.img, img_bgr,
                    f"{name}_map.html")

    if args.save:
        from supabase import create_client
        client = create_client(os.environ["SUPABASE_URL"],
                               os.environ["SUPABASE_SERVICE_KEY"])
        client.table("zone_boundaries").upsert({
            "project_name": name,
            "geojson": geojson["features"][0],
            "source": "img_extract",
        }, on_conflict="project_name").execute()
        print(f"Supabase 저장 완료: {name}")


if __name__ == "__main__":
    main()
