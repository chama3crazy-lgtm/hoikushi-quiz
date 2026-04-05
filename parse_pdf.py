"""1級電気工事施工管理技士 過去問PDFパーサー

TACサイトの過去問PDFをダウンロード・解析してJSONデータを生成する。
"""

import json
import os
import re
import time
import urllib.request

import fitz  # PyMuPDF（図抽出用）
import pdfplumber


# --- PDF URL定義（令和3年〜令和7年 第一次検定） ---
PDF_URLS = [
    ("令和3年度", "https://www.tac-school.co.jp/file/tac/kouza_denkikoujisekokan/2022pdf/R3_1-1.pdf", "R3"),
    ("令和4年度", "https://www.tac-school.co.jp/file/tac/kouza_denkikoujisekokan/2022pdf/R4_1-1.pdf", "R4"),
    ("令和5年度", "https://www.tac-school.co.jp/file/tac/kouza_denkikoujisekokan/2022pdf/R5_1-1.pdf", "R5"),
    ("令和6年度", "https://www.tac-school.co.jp/file/tac/kouza_denkikoujisekokan/2024pdf/R6_1-1.pdf", "R6"),
    ("令和7年度", "https://www.tac-school.co.jp/file/tac/kouza_denkikoujisekokan/2025pdf/R7_1-1.pdf", "R7"),
]

PDF_DIR = "pdfs"
IMG_DIR = "images"


# --- ユーティリティ ---

def clean_text(text):
    """CIDコード（ふりがな等）・印刷ノイズを除去してクリーンなテキストを返す"""
    if not text:
        return ""
    text = re.sub(r"\(cid:\d+\)", "", text)
    # InDesignメタデータ除去（DDAA--MM...indd... または DAM-電1AM.smd...）
    text = re.sub(r"DDAA[^\n]*", "", text)
    text = re.sub(r"DAM-[^\n]*", "", text)
    # ページ番号除去（− X − または - X -）
    text = re.sub(r"[\-\u2212\uff0d]\s*[0-9]+\s*[\-\u2212\uff0d]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def download_pdf(year_label, url, code):
    """PDFをダウンロード（既存ならスキップ）"""
    os.makedirs(PDF_DIR, exist_ok=True)
    filename = os.path.join(PDF_DIR, f"{code}.pdf")
    if os.path.exists(filename):
        print(f"  スキップ（既存）: {filename}")
        return filename
    print(f"  ダウンロード: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        with open(filename, "wb") as f:
            f.write(data)
        print(f"  保存: {filename} ({len(data):,} bytes)")
        time.sleep(1)
        return filename
    except Exception as e:
        print(f"  ダウンロード失敗: {e}")
        return None


# --- 正答肢パース ---

def parse_answer_key(text):
    """正答肢ページから {問題番号: 正答} の辞書を返す。
    ASCII数字のみ使用して全角数字のノイズを回避する。"""
    answers = {}
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"問題No\.?", line):
            # ASCII数字のみ抽出（全角数字・注釈を無視）
            after_no = line.split("問題No", 1)[1]
            nums = list(map(int, re.findall(r"[0-9]+", after_no)))
            # 正答肢行を探す（次に "正答肢" が含まれる行）
            j = i + 1
            while j < len(lines) and "正答肢" not in lines[j]:
                j += 1
            if j < len(lines) and "正答肢" in lines[j]:
                ans_part = lines[j].split("正答肢", 1)[1]
                # ASCII数字のみ抽出
                ans_nums = list(map(int, re.findall(r"[0-9]+", ans_part)))
                for q_num, ans in zip(nums, ans_nums[: len(nums)]):
                    answers[q_num] = ans
                i = j + 1
                continue
        i += 1
    return answers


# --- 図の抽出（PyMuPDFベース） ---

def extract_figure(pdf_path, page_idx, q_num, year_code):
    """指定ページから問題番号の図をクロップしてPNGで保存する。
    PyMuPDFのテキストブロックで正確なY座標を取得してクロップ。
    戻り値: 保存した画像ファイルのパス（失敗時None）"""
    os.makedirs(IMG_DIR, exist_ok=True)
    img_path = os.path.join(IMG_DIR, f"{year_code}_No{q_num:03d}.png")

    try:
        doc = fitz.open(pdf_path)
        if page_idx >= len(doc):
            doc.close()
            return None

        page = doc[page_idx]
        page_rect = page.rect

        # テキストブロックを取得（y0順にソート）
        blocks = sorted(page.get_text("blocks"), key=lambda b: b[1])

        # この問題番号のブロックとそのy1を探す
        # "〔No ． 3  〕" のように空白が入る場合を考慮
        q_marker_re = re.compile(rf"No\s*[．.]\s*{q_num}\s*(?:\s|[〕〕\]）])")
        q_block_y1 = None  # 問題マーカーブロックのy1

        for b in blocks:
            block_text = re.sub(r"\(cid:\d+\)", "", b[4])
            if q_marker_re.search(block_text):
                q_block_y1 = b[3]
                break

        if q_block_y1 is None:
            doc.close()
            return None

        # まず選択肢の開始y0を探す（問題マーカー以降で "1．" から始まるブロック）
        opt_y0 = None
        for b in blocks:
            if b[1] < q_block_y1:
                continue
            block_text = re.sub(r"\(cid:\d+\)", "", b[4]).strip()
            if re.match(r"1\s*[．.]\s", block_text):
                opt_y0 = b[1]
                break
            if re.search(r"〔No\s*[．.]\s*[0-9]+\s*[〕〕\]]", block_text):
                opt_y0 = b[1]
                break

        if opt_y0 is None:
            opt_y0 = page_rect.height * 0.85

        # 問題文の終端（図の開始）を探す
        # 優先: 「どれか。」「正しいものはどれか」などで終わる行のy1
        # それが見つからなければ: q_block_y1から近い範囲（50pt以内）の最後の意味あるブロック
        preamble_end_y1 = q_block_y1
        preamble_end_re = re.compile(r"どれか[。\s]?$|正しいもの.*$|適当なもの.*$|誤っているもの.*$")

        for b in blocks:
            if b[1] < q_block_y1:
                continue
            if b[1] >= opt_y0 - 2:
                break
            block_text = re.sub(r"\(cid:\d+\)", "", b[4]).strip()
            non_furigana = re.sub(r"[\u3040-\u309f\u30a0-\u30ff\s]", "", block_text)
            if len(non_furigana) < 2:
                continue  # ふりがなブロックをスキップ
            # 質問文末尾パターン
            if preamble_end_re.search(block_text):
                preamble_end_y1 = b[3]
                break  # 見つかったら停止（これが質問文終端）
            # q_block_y1から60pt以内ならまだ質問文
            if b[1] - q_block_y1 < 60:
                preamble_end_y1 = b[3]

        fig_y0 = preamble_end_y1 + 3
        fig_y1 = opt_y0 - 3

        if fig_y1 - fig_y0 < 15:
            doc.close()
            return None  # 図の範囲が小さすぎる

        # ページをレンダリングして図の領域をクロップ
        scale = 1.5
        mat = fitz.Matrix(scale, scale)
        margin = 30
        clip = fitz.Rect(
            page_rect.x0 + margin,
            fig_y0,
            page_rect.x1 - margin,
            fig_y1,
        )
        pix = page.get_pixmap(matrix=mat, clip=clip)
        pix.save(img_path)
        doc.close()
        return img_path

    except Exception as e:
        print(f"    図抽出失敗 No.{q_num}: {e}")
        return None


# --- 問題パース ---

def clean_block(text):
    """テキストブロック内の余分な空白・改行を整理する"""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    return " ".join(lines)


def parse_questions(pdf_path, year_label, year_code):
    """PDFから全問題を抽出してリストで返す。新旧フォーマット両対応。"""

    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        page_texts = []
        for page in pdf.pages:
            t = page.extract_text() or ""
            page_texts.append(clean_text(t))

    # 正答肢を取得（末尾数ページから探す）
    answer_key = {}
    for i in range(total_pages - 1, max(-1, total_pages - 6), -1):
        if "正答肢" in page_texts[i]:
            answer_key = parse_answer_key(page_texts[i])
            print(f"  正答肢: {len(answer_key)}問分取得")
            break

    # 午後の部の開始ページを検出
    gogo_page = None
    for i, t in enumerate(page_texts):
        if "午後の部" in t and i > 0:
            gogo_page = i
            break

    # 全ページテキストを結合（正答肢ページは除外）
    page_starts = []  # (char_offset, page_index)
    combined = ""
    for i, t in enumerate(page_texts):
        if "正答肢" in t and "〔No" not in t:
            continue
        page_starts.append((len(combined), i))
        combined += t + "\n"

    def get_page_for_pos(pos):
        result = 0
        for offset, pidx in page_starts:
            if pos >= offset:
                result = pidx
        return result

    # フォーマット検出（旧: R3-R5 では選択肢が "1阿" パターン）
    is_old_format = bool(re.search(r"[1-4]阿", combined[:3000]))
    print(f"  フォーマット: {'旧（R3-R5）' if is_old_format else '新（R6+）'}")

    if is_old_format:
        return _parse_old_format(
            combined, page_starts, get_page_for_pos, gogo_page,
            answer_key, year_label, year_code, pdf_path
        )
    else:
        return _parse_new_format(
            combined, page_starts, get_page_for_pos, gogo_page,
            answer_key, year_label, year_code, pdf_path
        )


def _parse_old_format(combined, page_starts, get_page_for_pos, gogo_page,
                      answer_key, year_label, year_code, pdf_path):
    """旧フォーマット（R3-R5）の問題を連番で抽出する"""

    # 問題開始を〔No で区切る
    # 選択肢は 1阿/2阿/3阿/4阿 パターン
    q_delimiter = re.compile(r"〔No[^\]〕]{0,15}[〕\]]")
    all_q_positions = list(q_delimiter.finditer(combined))

    questions = []
    q_sequential = 0  # 連番（answer_keyのキーに対応）

    for idx, match in enumerate(all_q_positions):
        pos = match.start()

        # 直後に「から」「まで」「の」「亜」などが続く場合は注釈行なのでスキップ
        next_chars = combined[match.end(): match.end() + 15].strip()
        if re.match(r"^(?:から|まで|のうち|の[0-9２３４５６７８９]|亜〔No)", next_chars):
            continue

        # ブロック終端を探す
        block_end = len(combined)
        for next_match in all_q_positions[idx + 1:]:
            nc = combined[next_match.end(): next_match.end() + 15].strip()
            if not re.match(r"^(?:から|まで|のうち)", nc):
                block_end = next_match.start()
                break

        block = combined[match.end(): block_end]

        # ブロック末尾のふりがな行を除去（次の問題のふりがなが混入するため）
        # ひらがなのみの行（次ページ先頭ふりがな）をブロック末尾から削除
        block = re.sub(r"(\n[\u3040-\u309f \t]+)+\s*$", "", block)

        # 選択肢の開始を見つける（1阿 パターン）
        opt_match = re.search(r"(?:^|\n)\s*1\s*阿\s*", block)
        if not opt_match:
            continue  # 選択肢なし→スキップ

        q_text_raw = block[: opt_match.start()]
        opts_text = block[opt_match.start():]

        # 選択肢を抽出
        options = []
        for opt_m in re.finditer(
            r"(?:^|\n)\s*[1-5]\s*阿\s*(.*?)(?=(?:\n\s*[1-5]\s*阿\s)|\Z)",
            opts_text,
            re.DOTALL,
        ):
            opt_text = clean_block(opt_m.group(1))
            # ひらがなのみの単語（ふりがな）を末尾から除去
            opt_text = re.sub(r"\s+[\u3040-\u309f\s]+$", "", opt_text)
            opt_text = re.sub(r"\s+", " ", opt_text).strip()
            if opt_text:
                options.append(opt_text)

        if len(options) < 2:
            continue

        # 問題テキストのクリーニング
        q_text = clean_block(q_text_raw)
        # 旧フォーマットでは "亜" が読点に相当する場合があるため置換
        q_text = q_text.replace("亜", "、")
        # ひらがなのみの単語（ふりがな: スペースで区切られた2-5文字のひらがな）を除去
        q_text = re.sub(r"(?<= )[\u3040-\u309f]{2,6}(?= |$)", "", q_text)
        q_text = re.sub(r"\s+", " ", q_text).strip()
        # 先頭の〔No..〕残滓のみを除去（単位〔m〕等は除去しない）
        q_text = re.sub(r"^〔?No[^\]〕]{0,15}[〕\]]\s*", "", q_text).strip()

        if len(q_text) < 5:
            continue  # 問題文が極端に短い場合はスキップ

        q_sequential += 1
        page_idx = get_page_for_pos(pos)
        section = "午後の部" if (gogo_page is not None and page_idx >= gogo_page) else "午前の部"
        has_figure = bool(re.search(r"図に示す|下図|次の図|図示|下記の図", q_text))

        entry = {
            "exam_year": year_label,
            "section": section,
            "question_number": f"No.{q_sequential}",
            "question_text": q_text,
            "options": options,
            "correct_answer": str(answer_key.get(q_sequential, "")),
            "has_image": has_figure,
            "image_file": None,
        }
        questions.append((entry, page_idx, q_sequential))

    print(f"  問題数: {len(questions)}")
    print("  図を抽出中...")
    for entry, page_idx, q_num in questions:
        if entry["has_image"]:
            img_path = extract_figure(pdf_path, page_idx, q_num, year_code)
            if img_path:
                entry["image_file"] = img_path
                print(f"    No.{q_num} → {img_path}")
            else:
                print(f"    No.{q_num} → 図抽出スキップ")

    return [e for e, _, _ in questions]


def _parse_new_format(combined, page_starts, get_page_for_pos, gogo_page,
                      answer_key, year_label, year_code, pdf_path):
    """新フォーマット（R6以降）の問題を抽出する"""

    q_pattern = re.compile(r"〔No[．.]\s*([0-9]+)\s*[〕〕]")
    all_q_matches = list(q_pattern.finditer(combined))
    questions = []

    for idx, match in enumerate(all_q_matches):
        q_num = int(match.group(1))
        pos = match.start()

        next_chars = combined[match.end(): match.end() + 10].strip()
        if re.match(r"^(?:から|まで|のうち)", next_chars):
            continue

        block_end = len(combined)
        for next_match in all_q_matches[idx + 1:]:
            nc = combined[next_match.end(): next_match.end() + 10].strip()
            if not re.match(r"^(?:から|まで|のうち)", nc):
                block_end = next_match.start()
                break

        block = combined[match.end(): block_end].strip()

        opt_match = re.search(r"(?:^|\n)\s*1[．.]\s", block)
        if opt_match:
            q_text = clean_block(block[: opt_match.start()])
            opts_text = block[opt_match.start():]
        else:
            q_text = clean_block(block)
            opts_text = ""

        options = []
        for opt_m in re.finditer(
            r"(?:^|\n)\s*[1-5][．.]\s+(.*?)(?=(?:\n\s*[1-5][．.]\s)|\Z)",
            opts_text,
            re.DOTALL,
        ):
            opt_text = clean_block(opt_m.group(1))
            options.append(opt_text)

        if len(options) < 2:
            continue

        q_text = re.sub(r"^〔?No[．.]\s*[0-9]+\s*〕?\s*", "", q_text).strip()

        page_idx = get_page_for_pos(pos)
        section = "午後の部" if (gogo_page is not None and page_idx >= gogo_page) else "午前の部"
        has_figure = bool(re.search(r"図に示す|下図|次の図|図示|下記の図", q_text))

        entry = {
            "exam_year": year_label,
            "section": section,
            "question_number": f"No.{q_num}",
            "question_text": q_text,
            "options": options,
            "correct_answer": str(answer_key.get(q_num, "")),
            "has_image": has_figure,
            "image_file": None,
        }
        questions.append((entry, page_idx, q_num))

    print(f"  問題数: {len(questions)}")
    print("  図を抽出中...")
    for entry, page_idx, q_num in questions:
        if entry["has_image"]:
            img_path = extract_figure(pdf_path, page_idx, q_num, year_code)
            if img_path:
                entry["image_file"] = img_path
                print(f"    No.{q_num} → {img_path}")
            else:
                print(f"    No.{q_num} → 図抽出スキップ")

    return [e for e, _, _ in questions]


# --- メイン ---

def main():
    all_questions = []

    for year_label, url, code in PDF_URLS:
        print(f"\n=== {year_label} ({code}) ===")
        pdf_path = download_pdf(year_label, url, code)
        if pdf_path is None:
            print(f"  スキップ: {year_label}")
            continue

        questions = parse_questions(pdf_path, year_label, code)
        all_questions.extend(questions)
        print(f"  → 累計 {len(all_questions)} 問")

    # JSON保存
    json_path = "denki_exam_data.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_questions, f, ensure_ascii=False, indent=2)
    print(f"\n完了: {len(all_questions)} 問 → {json_path}")

    # data.js生成
    generate_data_js(all_questions)


def generate_data_js(questions):
    """JSONデータからdata.jsを生成する"""
    json_str = json.dumps(questions, ensure_ascii=False, indent=2)
    js_content = f"// 1級電気工事施工管理技士 過去問データ\nconst EXAM_DATA = {json_str};\n"
    with open("data.js", "w", encoding="utf-8") as f:
        f.write(js_content)
    print(f"data.js 生成完了 ({len(questions)} 問)")


if __name__ == "__main__":
    main()
