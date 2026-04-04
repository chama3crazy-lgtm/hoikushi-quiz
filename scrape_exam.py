"""保育士試験過去問スクレイピング・JSON変換スクリプト"""

import json
import re
import time
import urllib.request
from html.parser import HTMLParser


# --- 年度・科目定義 ---
YEARS = [
    ("2021", "令和3年（前期）", "first", "r3_f"),
    ("2021", "令和3年（後期）", "later", "r3_l"),
    ("2022", "令和4年（前期）", "first", "r4_f"),
    ("2022", "令和4年（後期）", "later", "r4_l"),
    ("2023", "令和5年（前期）", "first", "r5_f"),
    ("2023", "令和5年（後期）", "later", "r5_l"),
    ("2024", "令和6年（前期）", "first", "r6_f"),
    ("2024", "令和6年（後期）", "later", "r6_l"),
    ("2025", "令和7年（前期）", "first", "r7_f"),
    ("2025", "令和7年（後期）", "later", "r7_l"),
]

# 科目定義（URL suffix → 科目名, JS answer key suffix）
# R3〜R6前期: 教育原理・社会的養護は統合ページ
SUBJECTS_COMBINED = [
    ("01_ho_ge", "保育原理", "t01"),
    ("02_kyo_yo", "教育原理・社会的養護", "t02"),
    ("03_ko_ka", "子ども家庭福祉", "t03"),
    ("04_sya_fu", "社会福祉", "t04"),
    ("05_ho_shi", "保育の心理学", "t05"),
    ("06_ko_ho", "子どもの保健", "t06"),
    ("07_ko_sy_ei", "子どもの食と栄養", "t07"),
    ("08_ho_ji", "保育実習理論", "t08"),
]

# R6後期以降: 教育原理と社会的養護が分離
SUBJECTS_SEPARATE = [
    ("01_ho_ge", "保育原理", "t01"),
    ("02_kyo_ge", "教育原理", "t02_kyo_ge"),
    ("02_sya_yo", "社会的養護", "t02_sya_yo"),
    ("03_ko_ka", "子ども家庭福祉", "t03"),
    ("04_sya_fu", "社会福祉", "t04"),
    ("05_ho_shi", "保育の心理学", "t05"),
    ("06_ko_ho", "子どもの保健", "t06"),
    ("07_ko_sy_ei", "子どもの食と栄養", "t07"),
    ("08_ho_ji", "保育実習理論", "t08"),
]


def build_page_list():
    """全ページのURL、年度ラベル、科目名、JS正解キーを返す"""
    pages = []
    for year_code, year_label, period, js_prefix in YEARS:
        is_separate = (year_code == "2024" and period == "later") or int(year_code) >= 2025
        subjects = SUBJECTS_SEPARATE if is_separate else SUBJECTS_COMBINED
        for url_suffix, subject_name, answer_key_suffix in subjects:
            url = f"https://hoikusi.biz/exam/{year_code}_{period}_{url_suffix}/"
            answer_key = f"{js_prefix}_{answer_key_suffix}"
            pages.append({
                "url": url,
                "year_label": year_label,
                "subject": subject_name,
                "js_prefix": js_prefix,
                "answer_key": answer_key,
            })
    return pages


# --- HTTP取得 ---
def fetch_page(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                print(f"  FAILED after {retries} retries: {e}")
    return None


# --- JSから正解を抽出 ---
def fetch_all_answers(js_prefix):
    """JSファイルから全科目の正解データを取得して辞書で返す"""
    url = f"https://hoikusi.biz/program/{js_prefix}_test.js"
    js = fetch_page(url)
    if js is None:
        return {}

    answers = {}

    # 新形式: const xxx_correctAnswers = [[3, 4], [3], ...];
    new_pattern = re.findall(
        r'const\s+(\w+)_correctAnswers\s*=\s*\[([\s\S]*?)\];',
        js
    )
    for name, body in new_pattern:
        # Parse array of arrays
        nums = re.findall(r'\[([^\]]*)\]', body)
        answer_list = []
        for n in nums:
            vals = [x.strip() for x in n.split(',') if x.strip()]
            if vals:
                answer_list.append(', '.join(vals))
            else:
                answer_list.append(None)
        answers[name] = answer_list

    # 旧形式: var xxxanser = [000, 4, 2, 3, ...];
    old_pattern = re.findall(
        r'var\s+(\w+)anser\s*=\s*\[([^\]]+)\]',
        js
    )
    for name, body in old_pattern:
        vals = [x.strip() for x in body.split(',')]
        # Index 0 is dummy (000), real answers start at index 1
        answer_list = []
        for v in vals[1:]:
            if v and v != '000':
                answer_list.append(v)
            else:
                answer_list.append(None)
        answers[name] = answer_list

    # text_all_correct_no (全員正解問題番号)
    all_correct_match = re.search(r'text_all_correct_no\s*=\s*(\d+)', js)
    all_correct_no = int(all_correct_match.group(1)) if all_correct_match else 999

    return answers, all_correct_no


# --- HTMLパーサー ---
class ExamPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_test_div = False
        self.in_question_p = False
        self.in_table = False
        self.in_td = False
        self.in_tr = False
        self.in_score_area = False
        self.in_p = False

        self.depth_test_div = 0
        self.depth_score_area = 0

        self.current_question_text = ""
        self.current_options = []
        self.current_row_cells = []
        self.questions = []
        self.has_image_in_current = False
        self.current_image_urls = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")

        # Skip score area entirely
        if "score_area_contents" in cls:
            self.in_score_area = True
            self.depth_score_area = 1
            return
        if self.in_score_area:
            if tag == "div":
                self.depth_score_area += 1
            return

        # Track test content div
        if "test_text_bg" in cls:
            self.in_test_div = True
            self.depth_test_div = 1
            return
        if not self.in_test_div:
            return

        if tag == "div":
            self.depth_test_div += 1
            # grade_area div indicates end of questions
            if "grade_area" in cls:
                if self.current_question_text:
                    self._save_question()
                self.in_test_div = False
                return

        if tag == "p" and "question" in cls:
            if self.current_question_text:
                self._save_question()
            self.in_question_p = True
            self.current_question_text = ""
            self.current_options = []
            self.has_image_in_current = False
        elif tag == "p":
            self.in_p = True
        elif tag == "table":
            self.in_table = True
        elif tag == "tr":
            self.in_tr = True
            self.current_row_cells = []
        elif tag == "td":
            self.in_td = True
            self.current_row_cells.append("")
        elif tag == "img":
            self.has_image_in_current = True
            src = attrs_dict.get("src", "")
            if src and "siteguard" not in src:
                self.current_image_urls.append(src)
        elif tag == "br":
            if self.in_question_p:
                self.current_question_text += "\n"
            elif self.in_p and self.current_question_text:
                self.current_question_text += "\n"

    def handle_endtag(self, tag):
        if self.in_score_area:
            if tag == "div":
                self.depth_score_area -= 1
                if self.depth_score_area <= 0:
                    self.in_score_area = False
            return

        if not self.in_test_div:
            return

        if tag == "div":
            self.depth_test_div -= 1
            if self.depth_test_div <= 0:
                self.in_test_div = False
                if self.current_question_text:
                    self._save_question()

        if tag == "p":
            if self.in_question_p:
                self.in_question_p = False
            self.in_p = False
        elif tag == "table":
            self.in_table = False
        elif tag == "tr":
            self.in_tr = False
            if self.current_row_cells:
                row_text = "\t".join(c.strip() for c in self.current_row_cells)
                if row_text.strip():
                    self.current_options.append(row_text.strip())
        elif tag == "td":
            self.in_td = False

    def handle_data(self, data):
        if self.in_score_area:
            return
        if not self.in_test_div:
            return

        if self.in_td:
            if self.current_row_cells:
                self.current_row_cells[-1] += data
        elif self.in_question_p:
            self.current_question_text += data
        elif self.in_p and self.current_question_text:
            text = data.strip()
            if text:
                self.current_question_text += "\n" + text

    def _save_question(self):
        text = self.current_question_text.strip()
        if not text:
            return

        # Skip compound question headers like "問 18・問 19　次の【事例】を..."
        # These are preamble for the following questions, not actual questions
        if re.match(r'問\s*\d+\s*・\s*問\s*\d+', text):
            # Store as preamble for next question
            m_pre = re.match(r'問\s*\d+\s*・\s*問\s*\d+[　\s]*([\s\S]*)', text)
            if m_pre:
                self._pending_preamble = m_pre.group(1).strip()
            self.current_question_text = ""
            self.current_options = []
            self.has_image_in_current = False
            self.current_image_urls = []
            return

        # Extract question number (half-width or full-width)
        m = re.match(r'問(\d+)[　\s]+([\s\S]*)', text)
        if not m:
            m = re.match(r'問([０-９]+)[　\s]+([\s\S]*)', text)
            if m:
                fw = m.group(1)
                q_num = str(int(fw.translate(str.maketrans('０１２３４５６７８９', '0123456789'))))
                q_text = m.group(2).strip()
            else:
                q_num = str(len(self.questions) + 1)
                q_text = text
        else:
            q_num = m.group(1)
            q_text = m.group(2).strip()

        # Prepend preamble (shared case text) if exists
        if hasattr(self, '_pending_preamble') and self._pending_preamble:
            q_text = self._pending_preamble + "\n\n" + q_text
            self._pending_preamble = ""

        clean_options = [opt.strip() for opt in self.current_options if opt.strip()]

        self.questions.append({
            "question_number": f"問{q_num}",
            "question_text": q_text,
            "options": clean_options,
            "has_image": self.has_image_in_current,
            "image_urls": list(self.current_image_urls),
        })
        self.current_question_text = ""
        self.current_options = []
        self.has_image_in_current = False
        self.current_image_urls = []


def parse_exam_page(html):
    """HTMLをパースして問題リストを返す"""
    parser = ExamPageParser()
    parser.feed(html)
    return parser.questions


def main():
    pages = build_page_list()
    all_questions = []
    total = len(pages)

    print(f"処理対象: {total} ページ")

    # JSファイルをキャッシュ（年度・期ごとに1つ）
    js_cache = {}

    for idx, page in enumerate(pages, 1):
        year_label = page["year_label"]
        subject = page["subject"]
        js_prefix = page["js_prefix"]
        answer_key = page["answer_key"]

        print(f"[{idx}/{total}] {year_label} {subject} ... ", end="", flush=True)

        # HTML取得・パース
        html = fetch_page(page["url"])
        if html is None:
            print("FAILED (HTML)")
            continue

        questions = parse_exam_page(html)

        # JS正解データ取得（キャッシュ）
        if js_prefix not in js_cache:
            result = fetch_all_answers(js_prefix)
            if result:
                js_cache[js_prefix] = result
            else:
                js_cache[js_prefix] = ({}, 999)
            time.sleep(0.3)

        answer_dict, all_correct_no = js_cache[js_prefix]

        # 正解データを問題に紐付け
        answer_list = answer_dict.get(answer_key, [])

        for i, q in enumerate(questions):
            answer = answer_list[i] if i < len(answer_list) else None

            # 全員正解の問題チェック
            q_num = int(re.search(r'\d+', q["question_number"]).group())
            if q_num == all_correct_no:
                answer = "全員正解"

            entry = {
                "exam_year": year_label,
                "subject": subject,
                "question_number": q["question_number"],
                "question_text": q["question_text"],
                "options": q["options"],
                "correct_answer": answer,
                "has_image": q["has_image"],
            }
            if q.get("image_urls"):
                entry["image_urls"] = q["image_urls"]
            all_questions.append(entry)

        print(f"{len(questions)}問 (正解{len(answer_list)}件)")
        time.sleep(0.5)

    # 保存
    output_path = "hoikushi_exam_data.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_questions, f, ensure_ascii=False, indent=2)

    print(f"\n完了: {len(all_questions)}問 → {output_path}")


if __name__ == "__main__":
    main()
