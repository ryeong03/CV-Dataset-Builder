#!/usr/bin/env python3
"""
Educational CV Dataset Builder (Naver Edition)
- Source: Naver Image Search (Selenium)
- Logic: Unsupervised Clustering (DBSCAN) + Semantic Filtering (CLIP)
- Goal: Create clean, educational datasets automatically.
"""
import sys
import io

# Windows cp949 대신 UTF-8로 stdout/stderr 사용 (UnicodeEncodeError 방지)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
else:
    try:
        if getattr(sys.stdout, "buffer", None) is not None:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        if getattr(sys.stderr, "buffer", None) is not None:
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

import argparse
import hashlib
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path
import numpy as np
import cv2
import torch
from PIL import Image
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from transformers import CLIPProcessor, CLIPModel
from sklearn.cluster import DBSCAN
from sklearn.metrics.pairwise import cosine_similarity

# --- 1. 네이버 이미지 수집기 (Selenium) ---
def crawl_naver_images(query, limit=100):
    print(f"[검색] 네이버에서 '{query}' 검색 중...")
    
    options = Options()
    options.add_argument("--headless=new") # 창 안 띄우고 실행
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    
    # 크롬 드라이버 자동 설치 및 실행
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    search_url = f"https://search.naver.com/search.naver?where=image&query={urllib.parse.quote(query)}"
    driver.get(search_url)
    time.sleep(2.0)  # 초기 이미지 로딩 대기

    # 스크롤 내리기 (이미지 로딩)
    for _ in range(5):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.2)
    last_height = driver.execute_script("return document.body.scrollHeight")
    for _ in range(10):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.0)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

    # 이미지 태그 찾기 (네이버 구조 변경 대비 여러 셀렉터 시도)
    images = []
    for selector in (
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
    ):
        images = driver.find_elements(By.CSS_SELECTOR, selector)
        if images:
            break
    candidates = []
    seen = set()
    for img in images:
        if len(candidates) >= limit * 2:
            break
        try:
            src = (
                img.get_attribute("data-lazy-src")
                or img.get_attribute("data-src")
                or img.get_attribute("data-source")
                or img.get_attribute("src")
            )
            if src and src.startswith("http") and src not in seen:
                seen.add(src)
                candidates.append({"url": src, "title": query})
        except Exception:
            continue
            
    driver.quit()
    print(f"[수집] 후보 이미지 {len(candidates)}개 발견!")
    return candidates

# --- 2. AI 두뇌 (CLIP 모델) ---
class Brain:
    def __init__(self):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[CLIP] AI 모델 로딩 중... ({self.device})")
        self.model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32").to(self.device)
        self.processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")

    def get_embedding(self, image: Image.Image) -> np.ndarray:
        inputs = self.processor(images=image, return_tensors="pt", padding=True).to(self.device)
        with torch.no_grad():
            outputs = self.model.get_image_features(**inputs)
        return outputs.cpu().numpy().flatten()

# --- 3. 유틸리티 (다운로드 & 변환) ---
def download_image(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read()
            nparr = np.frombuffer(data, np.uint8)
            img_cv2 = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            img_rgb = cv2.cvtColor(img_cv2, cv2.COLOR_BGR2RGB)
            return Image.fromarray(img_rgb), img_cv2, data
    except:
        return None, None, None

def quality_check(img_cv2, min_size=300):
    h, w = img_cv2.shape[:2]
    if w < min_size or h < min_size: return False
    gray = cv2.cvtColor(img_cv2, cv2.COLOR_BGR2GRAY)
    blur = cv2.Laplacian(gray, cv2.CV_64F).var()
    if blur < 50: return False # 너무 흐리면 탈락
    return True

# --- 4. 메인 로직 ---
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query", help="검색어 (예: 아자핑)")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--out_dir", default="data/naver_collected")
    args = parser.parse_args()
    
    # 1. 수집
    candidates = crawl_naver_images(args.query, args.limit)
    brain = Brain()
    
    valid_data = []
    embeddings = []
    
    print("[분석] 이미지 분석 및 임베딩 추출 중...")
    for cand in candidates:
        pil, cv2_img, raw = download_image(cand['url'])
        if pil is None: continue
        
        if not quality_check(cv2_img): continue
        
        vec = brain.get_embedding(pil)
        valid_data.append({"cv2": cv2_img, "url": cand['url']})
        embeddings.append(vec)
        print(f"\r진행률: {len(valid_data)}장 처리", end="")
        
    # 2. 클러스터링 (다수결)
    if not embeddings: return
    X = np.array(embeddings)
    X = X / np.linalg.norm(X, axis=1, keepdims=True)
    
    # DBSCAN으로 '진짜' 그룹 찾기
    clustering = DBSCAN(eps=0.18, min_samples=3, metric='cosine').fit(X)
    labels = clustering.labels_
    
    unique_labels = set(labels)
    if -1 in unique_labels: unique_labels.remove(-1) # 노이즈 제거
    
    if not unique_labels:
        print("\n[경고] 뚜렷한 특징을 못 찾았습니다.")
        return

    best_label = max(unique_labels, key=list(labels).count)
    print(f"\n[저장] '진짜 {args.query}' 그룹(ID:{best_label}) 확정! 저장 시작...")
    
    # 3. 저장 (폴더·파일명은 영문만 사용해 한글/인코딩 이슈 방지)
    out_path = Path(args.out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    manifest_path = out_path / "manifest.jsonl"

    count = 0
    with manifest_path.open("w", encoding="utf-8") as f:
        for i, item in enumerate(valid_data):
            if count >= args.limit:
                break
            if labels[i] == best_label:
                count += 1
                fname = f"img_{count:04d}.jpg"
                cv2.imwrite(str(out_path / fname), item["cv2"])
                meta = {"query": args.query, "file": fname, "source": "naver"}
                f.write(json.dumps(meta, ensure_ascii=False) + "\n")
                
    print(f"[완료] 총 {count}장 저장됨: {out_path}")

if __name__ == "__main__":
    main()