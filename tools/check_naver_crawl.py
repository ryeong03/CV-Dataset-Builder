#!/usr/bin/env python3
"""네이버 이미지 크롤만 테스트 (CLIP/저장 없음). 수집이 안 될 때 원인 확인용."""
import sys
import urllib.parse
from pathlib import Path

# 프로젝트 루트
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time

def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "아자핑"
    print(f"[테스트] 네이버 이미지 검색: '{query}' (크롤만, 저장 없음)\n")

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    url = f"https://search.naver.com/search.naver?where=image&query={urllib.parse.quote(query)}"
    driver.get(url)
    time.sleep(3)

    # 각 셀렉터별로 몇 개 나오는지 출력
    selectors = [
        ".image_tile_item img",
        "img._image._listImage",
        "img._img",
        ".photowall img",
        "div.photowall._photoGridWrapper img",
        ".photo_bx img",
        "a.thumb._thumb img",
        "#_sau_imageTab img[data-lazy-src]",
        "#_sau_imageTab img[data-source]",
        "#_sau_imageTab img[src*='http']",
        "img[data-lazy-src]",
        "img[data-source]",
        "img[src^='https://']",
    ]
    for sel in selectors:
        try:
            el = driver.find_elements(By.CSS_SELECTOR, sel)
            n = len(el)
            if n > 0:
                print(f"  OK  {sel!r} -> {n}개")
            else:
                print(f"  --  {sel!r} -> 0개")
        except Exception as e:
            print(f"  ERR {sel!r} -> {e}")

    # 스크롤 후 다시
    print("\n스크롤 후 재확인...")
    for _ in range(3):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.5)
    for sel in selectors[:5]:
        el = driver.find_elements(By.CSS_SELECTOR, sel)
        if el:
            print(f"  스크롤 후 {sel!r} -> {len(el)}개")
            break

    # 실제 후보 수 (http로 시작하는 URL만)
    candidates = []
    seen = set()
    for sel in selectors:
        images = driver.find_elements(By.CSS_SELECTOR, sel)
        for img in images[:200]:
            try:
                src = img.get_attribute("data-lazy-src") or img.get_attribute("data-src") or img.get_attribute("data-source") or img.get_attribute("src")
                if src and src.startswith("http") and src not in seen:
                    seen.add(src)
                    candidates.append(src)
            except Exception:
                pass
        if len(candidates) >= 40:
            break

    driver.quit()
    print(f"\n[결과] 유효한 이미지 URL 후보: {len(candidates)}개")
    if len(candidates) == 0:
        print("  -> 후보 0개면 네이버 페이지 구조가 바뀌었거나, 헤드리스가 차단된 것일 수 있음.")
        print("  -> Chrome을 보이는 모드로 한 번 테스트해 보려면 high_quality_image_collector.py에서 headless 주석 처리.")
    return 0 if candidates else 1

if __name__ == "__main__":
    sys.exit(main())
