#!/usr/bin/env python3
"""Build clean recipe data + images from the saved Daum (다음 요리) HTML pages.

Reads every recipes/*.html (except index.html) and produces:
  recipes/recipes-data.js       window.RECIPES        (listing for index.html)
  recipes/recipes-content.js    window.RECIPES_CONTENT (full content for recipe.html)
  recipes/img/<id>/NN.jpg       optimized step photos (~680px)
  recipes/thumbs/<id>.jpg       4:3 hero thumbnail for the index grid

Re-runnable: regenerates everything from the source HTML. The saved HTML files
are the source of truth and are left untouched.

Usage:  python3 build_recipes.py           (dry run: stats only)
        python3 build_recipes.py --write    (write data + images)
"""
import os, re, sys, json, base64, html as htmllib, io, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(HERE, "img")
THUMB_DIR = os.path.join(HERE, "thumbs")
DRY = "--write" not in sys.argv

STEP_W, STEP_Q = 680, 76          # inline step photos
THUMB_W, THUMB_H, THUMB_Q = 520, 390, 74

CATEGORIES = [
    ("soup",    "Soups & Stews",     "국·탕·찌개",
     ["찌개","전골","육개장","육계장","곰탕","국밥","미역국","순두부","해장국","떡국","부대","설렁탕",
      "매운탕","해물탕","감자탕","삼계탕","된장국","콩나물국","북엇국","우거지","시래기국","사골","스키야끼",
      "샤브","국물","탕","스프","수프"]),
    ("rice",    "Rice & Noodles",    "밥·면",
     ["국수","우동","라면","쫄면","냉면","파스타","볶음밥","비빔밥","비빔국수","떡볶이","수제비","죽",
      "필라프","리조또","리조토","김밥","주먹밥","덮밥","월남쌈","비빔","묵밥","영양밥","유부초밥","초밥",
      "라볶이","돈부리","돈브리","빠에야","파에야","톳밥","시래기밥"]),
    ("dessert", "Desserts & Baking", "디저트·베이킹",
     ["케이크","케익","케잌","쿠키","빵","식빵","푸딩","슈","인절미","설기","크림","파이","젤리","마카롱",
      "스콘","타르트","컵케익","무스","초콜릿","초콜렛","브라우니","롤케","머핀","도넛","와플","카스테라",
      "제과","베이킹","떡"]),
    ("grill",   "Grilled & Fried",   "구이·튀김·전",
     ["구이","튀김","부침","돈까스","돈가스","까스","바베큐","스테이크","그라탕","전","꼬치","커틀릿",
      "프라이","부꾸미","구워"]),
    ("braise",  "Braised & Steamed", "조림·찜·볶음",
     ["조림","찜","볶음","불고기","장조림","데리야끼","제육","보쌈","수육","편육","삶","유산슬"]),
    ("side",    "Sides & Pickles",   "반찬·김치·장아찌",
     ["무침","나물","장아찌","짱아찌","김치","피클","겉절이","샐러드","게장","젓갈","쌈장","깍두기",
      "단무지","묵","쌈","말이","밑반찬","반찬"]),
]

HEAD_WORDS = {
    "재료","요리재료","준비재료","준비물","주재료","부재료",
    "만들기","만드는법","만드는 법","만드는방법","만드는 방법",
    "조리법","조리순서","조리방법","요리방법","레시피",
    "양념","양념장","양념재료","소스","소스재료","육수",
    "tip","팁","포인트","요리팁","cooking tip",
}


def categorize(title):
    for key, _en, _ko, kws in CATEGORIES:
        for kw in kws:
            if kw in title:
                return key
    return "other"


def clean_title(raw):
    t = htmllib.unescape(raw).strip()
    return re.sub(r'\s*-\s*다음\s*요리\s*$', '', t).strip()


def strip_tags(s):
    return htmllib.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', s))).strip()


def parse_minutes(t):
    if not t:
        return 999
    h = re.search(r'(\d+)\s*시간', t)
    m = re.search(r'(\d+)\s*분', t)
    total = (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
    return total or 999


def extract_meta(html):
    out = {}
    anchor = html.find('요리재료')
    if anchor < 0:
        anchor = html.find('조리시간')
    if anchor < 0:
        return out
    seg = html[max(0, anchor - 800):anchor + 1200]
    for dt, dd in re.findall(r'<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>', seg, re.S):
        label = strip_tags(dt)
        if label:
            out[label] = strip_tags(dd)
    return out


def mk_text(t):
    t = re.sub(r'\s+', ' ', t).strip()
    low = t.lower().rstrip(':').strip()
    is_head = (
        bool(re.fullmatch(r'[\[\<【\(].{1,24}[\]\>】\)]', t)) or
        (len(t) <= 16 and t.endswith(':')) or
        (low in HEAD_WORDS) or
        (re.sub(r'[\s★☆▶◆■●#*\-~]', '', low) in HEAD_WORDS)
    )
    return {"t": "h", "v": t} if is_head else {"t": "p", "v": t}


def extract_blocks(raw):
    """Return (blocks, image_datauris) in document order."""
    marker = '<div class="tx-content-container">'
    start = raw.find(marker)
    if start < 0:
        return [], []
    depth = 0
    end = raw.find('CT_ZONE_otherRecipe', start)
    for m in re.finditer(r'(?i)<div\b|</div>', raw[start:]):
        depth += 1 if m.group(0).lower().startswith('<div') else -1
        if depth == 0:
            end = start + m.start()
            break
    seg = raw[start + len(marker):end]

    seg = re.sub(r'(?is)<style\b[^>]*>.*?</style>', ' ', seg)
    seg = re.sub(r'(?is)<script\b[^>]*>.*?</script>', ' ', seg)
    seg = re.sub(r'(?s)<!--.*?-->', ' ', seg)

    imgs = []
    def repl(m):
        s = re.search(r'src="(data:image/[^;]+;base64,[^"]+)"', m.group(0))
        if s and len(s.group(1)) > 3000:
            imgs.append(s.group(1))
            return "\n\x00IMG%d\x00\n" % (len(imgs) - 1)
        return " "
    seg = re.sub(r'<img\b[^>]*>', repl, seg)

    seg = re.sub(r'(?i)</(p|div|h[1-6]|li|tr|table|blockquote)>', '\n', seg)
    seg = re.sub(r'(?i)<br\s*/?>', '\n', seg)
    seg = re.sub(r'<[^>]+>', '', seg)
    seg = htmllib.unescape(seg).replace('​', '').replace('\xa0', ' ')

    blocks = []
    for line in seg.split('\n'):
        for part in re.split(r'(\x00IMG\d+\x00)', line):
            part = part.strip()
            if not part:
                continue
            mi = re.fullmatch(r'\x00IMG(\d+)\x00', part)
            if mi:
                blocks.append({"t": "img", "i": int(mi.group(1))})
            else:
                blocks.append(mk_text(part))
    return blocks, imgs


def decode(datauri):
    from PIL import Image
    _, b64 = datauri.split(',', 1)
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")


def save_step(im, path):
    if im.width > STEP_W:
        im = im.resize((STEP_W, round(im.height * STEP_W / im.width)), 1)  # LANCZOS=1
    im.save(path, "JPEG", quality=STEP_Q, optimize=True, progressive=True)
    return os.path.getsize(path)


def save_thumb(im, path):
    w, h = im.size
    tr = THUMB_W / THUMB_H
    if w / h > tr:
        nw = int(h * tr); x = (w - nw) // 2; im = im.crop((x, 0, x + nw, h))
    else:
        nh = int(w / tr); y = (h - nh) // 2; im = im.crop((0, y, w, y + nh))
    im.resize((THUMB_W, THUMB_H), 1).save(path, "JPEG", quality=THUMB_Q, optimize=True)


def main():
    files = sorted(f for f in os.listdir(HERE) if f.endswith('.html') and f != 'index.html')
    if not DRY:
        for d in (IMG_DIR, THUMB_DIR):
            if os.path.isdir(d):
                shutil.rmtree(d)
            os.makedirs(d)

    listing, content, cat_count = [], {}, {}
    total_img_bytes, total_imgs, skipped = 0, 0, 0

    for n, fn in enumerate(files, 1):
        raw = open(os.path.join(HERE, fn), encoding='utf-8', errors='ignore').read()
        rid = f"r{n:03d}"
        tm = re.search(r'<title>(.*?)</title>', raw, re.S)
        title = clean_title(tm.group(1)) if tm else clean_title(os.path.splitext(fn)[0])
        meta = extract_meta(raw)
        cat = categorize(title)
        cat_count[cat] = cat_count.get(cat, 0) + 1
        sauce = meta.get('소스재료', '')
        if sauce.strip() == '-':
            sauce = ''
        tags = [t.strip() for t in re.split(r'[,\n]', meta.get('태그', ''))
                if t.strip() and t.strip() != '-']
        time_ = meta.get('조리시간', '')

        blocks, imgs = extract_blocks(raw)

        # save images, remap block indices -> file paths
        rel_paths = {}
        if not DRY:
            rdir = os.path.join(IMG_DIR, rid)
            os.makedirs(rdir, exist_ok=True)
        seq = 0
        for idx, uri in enumerate(imgs):
            rel = f"img/{rid}/{seq:02d}.jpg"
            if not DRY:
                try:
                    im = decode(uri)
                    total_img_bytes += save_step(im, os.path.join(HERE, rel))
                    if seq == 0:
                        save_thumb(im, os.path.join(THUMB_DIR, rid + ".jpg"))
                except Exception as e:
                    print(f"  IMG FAIL {rid} #{idx}: {e}")
                    skipped += 1
                    continue
            rel_paths[idx] = rel
            seq += 1
        total_imgs += seq

        # build ordered blocks with image paths; pull first image out as hero
        hero = ""
        out_blocks = []
        for b in blocks:
            if b["t"] == "img":
                p = rel_paths.get(b["i"]) if not DRY else f"img/{rid}/{b['i']:02d}.jpg"
                if not p:
                    continue
                if not hero:
                    hero = p           # first image becomes the hero, not repeated inline
                    continue
                out_blocks.append({"t": "img", "src": p})
            else:
                out_blocks.append(b)

        thumb = f"thumbs/{rid}.jpg" if imgs else ""
        listing.append({
            "id": rid, "title": title, "cat": cat, "time": time_,
            "mins": parse_minutes(time_), "difficulty": meta.get('난이도', ''),
            "ingredients": meta.get('요리재료', ''), "tags": tags, "thumb": thumb,
        })
        content[rid] = {
            "id": rid, "title": title, "cat": cat, "time": time_,
            "mins": parse_minutes(time_), "difficulty": meta.get('난이도', ''),
            "serving": meta.get('분량', ''), "ingredients": meta.get('요리재료', ''),
            "sauce": sauce, "tags": tags, "hero": hero, "blocks": out_blocks,
        }
        if n % 25 == 0:
            print(f"  ...{n}/{len(files)}")

    cats_out = [{"key": k, "en": en, "ko": ko, "count": cat_count.get(k, 0)}
                for (k, en, ko, _) in CATEGORIES]
    cats_out.append({"key": "other", "en": "More", "ko": "그 외", "count": cat_count.get("other", 0)})

    print(f"\nrecipes: {len(files)} | categories: {cat_count}")
    print(f"step images: {total_imgs} | skipped: {skipped}")
    if not DRY:
        print(f"image bytes: {total_img_bytes/1e6:.1f} MB")
        with open(os.path.join(HERE, "recipes-data.js"), "w", encoding="utf-8") as fh:
            fh.write("// Auto-generated by build_recipes.py — do not edit by hand.\n")
            fh.write("window.RECIPES = " +
                     json.dumps({"recipes": listing, "categories": cats_out},
                                ensure_ascii=False, separators=(',', ':')) + ";\n")
        with open(os.path.join(HERE, "recipes-content.js"), "w", encoding="utf-8") as fh:
            fh.write("// Auto-generated by build_recipes.py — do not edit by hand.\n")
            fh.write("window.RECIPES_CONTENT = " +
                     json.dumps(content, ensure_ascii=False, separators=(',', ':')) + ";\n")
        dz = os.path.getsize(os.path.join(HERE, "recipes-data.js")) / 1024
        cz = os.path.getsize(os.path.join(HERE, "recipes-content.js")) / 1024
        print(f"recipes-data.js: {dz:.0f} KB | recipes-content.js: {cz:.0f} KB")
        print("done.")


if __name__ == "__main__":
    main()
