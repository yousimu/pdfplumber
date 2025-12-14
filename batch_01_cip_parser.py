# -*- coding: utf-8 -*-
"""
一键抽取 PDF 图书元数据 —— 版权页交给本地 Ollama 解析
用法: python pdf2meta_ollama.py xxx.pdf
输出: output/xxx.json  +  output/the-art-of-xxx.md
"""
import fitz, re, json, os, sys, requests
from slugify import slugify
from rapidocr_onnxruntime import RapidOCR
from typing import Dict, List, Union, Any
from pathlib import Path
# 导入公共模型检查工具
from _utils import logger, check_model_exists, get_pdf_page_count, get_safe_title, get_text_md5
from _utils import OLLAMA_BASE_URL, OLLAMA_MODEL, WIKI_BASE_PATH, DEEPSEEK_API_KEY,PROXIES

# 立即执行检查
check_model_exists(OLLAMA_BASE_URL, OLLAMA_MODEL)

# ------------ 变量 -------------
OCR_DPI    = 200
MAX_FILENAME_LENGTH = 255 # 生成的文件名超长处理

# 脚本所在路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 基于基础目录定义存储路径
COVERS_DIR = os.path.join(WIKI_BASE_PATH, "covers")
META_DIR = os.path.join(WIKI_BASE_PATH, "meta")
MD_DIR = WIKI_BASE_PATH

if not os.path.exists(MD_DIR):
    raise SystemExit(1)

# -------------- OCR + 版权页定位 --------------
ocr_engine = RapidOCR()
def find_copyright_page(pdf_path):
    doc = fitz.open(pdf_path)
    
    # 限制搜索范围为前10页或总页数（取较小值）
    max_pages = min(10, doc.page_count)
    
    # 存储所有已解析页面的文字内容
    all_text_before_copyright = []
    copyright_found = False
        
    # 1. 先找文字页
    for idx, page in enumerate(doc):
        # 如果超出限制页数则停止
        if idx >= max_pages:
            break
            
        txt = page.get_text()
   
        # 检查是否有明显的版权标志
        if ("©" in txt or 
            "Copyright" in txt or 
            "copyright" in txt.lower() or
            "ISBN" in txt or
            "isbn" in txt.lower()):
            copyright_found = True
        
        # 如果发现了版权标志并且文本中包含ISBN相关信息
        if copyright_found and ("ISBN" in txt or "isbn" in txt.lower()):
            # 找到版权页，返回之前所有页面的文字加上当前页文字
            all_text_before_copyright.append(txt)
            return "\n".join(all_text_before_copyright)
        elif copyright_found and ("©" in txt or "Copyright" in txt or "copyright" in txt.lower()):
            # 找到版权页，返回之前所有页面的文字加上当前页文字
            all_text_before_copyright.append(txt)
            return "\n".join(all_text_before_copyright)
        else:
            # 未找到版权页，将当前页面文字加入列表
            all_text_before_copyright.append(txt)
            
    # 2. OCR 前 5 页（或者不超过max_pages）
    if not txt : # 只有在没有文本返回的情况下，才执行OCR
        logger.info(f"[info] 在前 {max_pages} 页面没有找到版权信息. Trying OCR...")
        for idx in range(min(5, max_pages)):
            pix = doc.load_page(idx).get_pixmap(dpi=OCR_DPI)
            result, _ = ocr_engine(pix.tobytes("png"))
            # 修复：检查result是否为None
            if result is None:
                logger.warn(f"OCR未能识别第 {idx+1} 页内容，可能是空白页，请检查文件。")
                continue
            txt = " ".join([line[1] for line in result])
            # 检查OCR结果是否为版权页
            if "©" in txt.lower() or "isbn" in txt.lower():
                # 找到版权页，返回之前所有页面的文字
                return "\n".join(all_text_before_copyright)
            else:
                # 如果前面没有通过文本提取找到版权页，这里也要注意避免重复添加
                # 因为OCR的是前3页，而文本提取可能是全部前10页
                # 为简化处理，这里不再添加OCR内容到all_text_before_copyright
                pass
            
    # 如果没找到版权页，返回已收集的前几页内容
    return "\n".join(all_text_before_copyright) if all_text_before_copyright else ""


# -------------- 提示词 --------------
TRANS_SYS_PROMPT = (
    """
    You are a professional translation consultant specializing in providing guidance for technical book translations. 
    Your task involves translating English content into Chinese（简体中文）, covering book titles, introductions, tables of contents, and main text. 
    The technical books span fields including information technology, cybersecurity, cloud computing, artificial intelligence, economics, management, and leadership.
    Output only the translated text without any explanations, parentheses, quotes, notes, clarifications, or additional punctuation.
    """
)

TRANS_SYS_PROMPT_ZH = (
    """
    你是一个专业的翻译顾问，专门为技术书籍翻译提供指导；
    你负责将[英文]翻译成[简体中文]，包括书籍名称、简介、目录和正文文本；
    技术书籍涉及的领域包括：信息技术、网络安全、云计算、人工智能、经济学、管理学、领导力等；
    仅输出译文，禁止任何解释、括号、引号、备注、说明、标点扩展
    """
)

# 翻译提示词
# 请将以下内容翻译成简体中文，仅输出译文，禁止任何解释、括号、引号、备注、说明、标点扩展：
TRANS_USR_PROMPT = (
    """
    Translate the following English text into Chinese;
    output only the translated text without any explanations, parentheses, quotes, notes, clarifications, or additional punctuation:
    """
)

# 解析版权页提示词
TOC_SYS_PROMPT = (
    """ You are a bibliographic expert.
        The following text is a copyright page.
        You MUST return a single-line JSON only, with NO explanation, NO markdown code block, NO comments.
        Required keys with sample values:
        {"title": "Economics", "subtitle": "", "authors": ["Stephen L. Slavin"], "isbn": "9780073511429", "publisher": "McGraw-Hill/Irwin", "publishedDate": "2011", "country": "US", "edition": 10}
        
        Important Rules:
        1. Always return valid JSON in one line
        2. Never include any text other than the JSON
        3. Never wrap JSON in markdown code blocks (no ```json)
        4. If you cannot find a value, use appropriate defaults:
           - title: "" (empty string, required)
           - subtitle: "" (empty string)
           - authors: [] (empty array)
           - isbn: "" (empty string)
           - publisher: "" (empty string)
           - publishedDate: "" (empty string)
           - country: "" (empty string)
           - edition: 1 (number)
        5. Extract edition as a number (e.g., "Tenth Edition" -> 10)
        6. Format publishedDate as YYYY (4 digits)
        7. Extract all authors into the array
        8. Remove any non-digit characters from ISBN
    """
)

# -------------- 大模型元数据解析 --------------
def parse_metadata_llm(cip_text: str, meta:Dict[str, Any], is_book = True):
    try:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": TOC_SYS_PROMPT + "\n\n" + cip_text,
            "stream": False
        }
        resp = requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload, timeout=300)
        resp.raise_for_status()
        response_data = resp.json()
        raw = response_data.get("response", "").strip()
        
        # 检查响应是否为空
        if not raw:
            logger.warn("Ollama API 返回空响应")
            return meta  # 返回原始meta而不报错
            
        # 去掉可能的 ```json 包裹
        raw = re.sub(r"^```json\s*|\s*```$", "", raw.strip())
        
        # 检查清理后的JSON字符串是否为空
        if not raw:
            logger.warn("清理后的响应为空")
            return meta  # 返回原始meta而不报错
            
        # 尝试解析JSON
        try:
            cip_json = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warn(f"JSON 解析失败，原始响应内容: {response_data.get('response', '')}")
            # 尝试从非标准响应中提取信息
            extracted_meta = extract_meta_from_non_json_response(response_data.get('response', ''))
            if extracted_meta:
                # 合并提取的信息到meta中
                for key, value in extracted_meta.items():
                    meta[key] = value
                logger.info(f"成功从非标准响应中提取元数据: " + str(extracted_meta))
            # 如果响应看起来不像JSON，直接返回原始meta
            return meta
        
        meta = _process_cip_fields("title", cip_json,meta)
        meta = _process_cip_fields("subtitle", cip_json,meta)
        meta = _process_cip_fields("authors", cip_json,meta)
        meta = _process_cip_fields("isbn", cip_json,meta)
        meta = _process_cip_fields("publisher", cip_json,meta)
        meta = _process_cip_fields("publishedDate", cip_json,meta)
        meta = _process_cip_fields("edition", cip_json,meta)
        
        if is_book:
            # 大模型解析的 ISBN 多个是数组，单个是字符串，需要处理
            meta["isbn"] = llm_handle_isbn(meta.get("isbn"))

            # 获取 edition
            edition_value = meta.get("edition")
            if edition_value is not None:
                try:
                    meta["edition"] = int(edition_value)
                except (ValueError, TypeError):
                    # 尝试通过大模型从版权页，再次专门提取 edition
                    edition = llm_handle_edition(cip_text)
                    if edition:
                        meta["edition"] = edition
                        
        if meta.get("authors"):
            meta["authors"] = handle_llm_authors(meta.get("authors"))
        
    except Exception as e:
        logger.error(f"大模型解析失败: {e}")
        # 不抛出异常，而是返回原始meta，使程序能够继续执行
        return meta
    
    logger.info(f"大模型解析版权页 ➜ " + str(meta))
    return meta

def _process_cip_fields (key:str, cip_json:Dict, meta:Dict):
    try:
        if cip_json.get(key):
            meta[key] = cip_json[key]
    except Exception as e:
        logger.warn(f"[warn] 处理 {key} 字段时出错: {e}")
    
    return meta

# 添加新函数：从非标准响应中提取元数据
def extract_meta_from_non_json_response(response_text: str) -> Dict[str, Any]:
    """
    从大模型的非标准响应中提取元数据
    """
    if not response_text:
        return {}
    
    meta = {}
    
    # 提取标题 (寻找类似 "Economics" 这样的书名)
    # 通常在文本开头附近能找到标题
    lines = response_text.split('\n')
    first_lines = ' '.join(lines[:10])  # 前10行
    
    # 查找类似 "Economics/Stephen L. Slavin" 这样的模式
    title_match = re.search(r'"([^"]+)"\s*/\s*([^/\n]+)', first_lines)
    if title_match:
        meta["title"] = title_match.group(1).strip()
        # 可以尝试提取作者，但已有专门处理作者的函数
    
    # 如果上面的方式没找到，在单独查找标题
    if not meta.get("title"):
        # 查找明显是书名的行
        for line in lines[:15]:  # 前15行中查找
            line = line.strip()
            # 如果一行较短且不包含特殊词汇，可能是标题
            if 3 < len(line) < 100 and not any(word in line.lower() for word in 
                ['based on', 'details provided', 'textbook', 'contents', 'information', 'request', 'question']):
                # 检查是否看起来像书名（首字母大写等）
                if re.match(r'^[A-Z][^A-Z]*[a-z]$', line) or re.match(r'^[A-Z][a-zA-Z\s\-:]+$', line):
                    meta["title"] = line
                    break
    
    # 提取作者
    author_patterns = [
        r'(?:Author|Written by|By)[:\s]+([^\n]+)',
        r'([A-Z][a-z]+(?:\s+[A-Z]\.?\s*[A-Z][a-z]+)+)'
    ]
    
    for pattern in author_patterns:
        author_match = re.search(pattern, response_text)
        if author_match:
            author_str = author_match.group(1).strip()
            meta["authors"] = [author_str]  # 简化处理，后续会由handle_llm_authors处理
            break
    
    # 提取ISBN
    isbn_match = re.search(r'(?:ISBN|isbn)[:\s]*([0-9Xx\-]+)', response_text)
    if isbn_match:
        # 清理ISBN，只保留数字和X
        isbn_clean = re.sub(r'[^0-9Xx]', '', isbn_match.group(1))
        if len(isbn_clean) in [10, 13]:
            meta["isbn"] = isbn_clean
    
    # 提取出版年份
    year_match = re.search(r'Copyright.*?(\d{4})', response_text)
    if year_match:
        meta["publishedDate"] = year_match.group(1)
    
    # 提取出版社
    publisher_patterns = [
        r'(?:Published by|Publisher)[:\s]+([^\n]+)',
        r'(McGraw-Hill[/\s][^\n]+)',
        r'((?:Prentice|Pearson|Addison|Wesley|Springer|Cambridge)[^\n]*)'
    ]
    
    for pattern in publisher_patterns:
        pub_match = re.search(pattern, response_text, re.IGNORECASE)
        if pub_match:
            meta["publisher"] = pub_match.group(1).strip()
            break
    
    # 提取版次
    edition_match = re.search(r'(?:Edition|edition)[:\s]*(\d+)(?:st|nd|rd|th)?', response_text, re.IGNORECASE)
    if edition_match:
        try:
            meta["edition"] = int(edition_match.group(1))
        except ValueError:
            pass
    
    # 从文本中提取版次（如 Tenth Edition -> 10）
    if not meta.get("edition"):
        edition_words = {
            'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5,
            'sixth': 6, 'seventh': 7, 'eighth': 8, 'ninth': 9, 'tenth': 10,
            'eleventh': 11, 'twelfth': 12, 'thirteenth': 13, 'fourteenth': 14, 'fifteenth': 15
        }
        for word, num in edition_words.items():
            if re.search(r'\b' + word + r'\s+edition\b', response_text, re.IGNORECASE):
                meta["edition"] = num
                break
    
    return meta

def handle_llm_authors(authors: Union[str, List[str]]) -> List[str]:
    """
    规范化作者信息，处理不同的输入格式，将其统一为列表格式
    
    Args:
        authors: 作者信息，可能是字符串或字符串列表
        
    Returns:
        List[str]: 标准化后的作者列表
        
    Examples:
        >>> handle_llm_authors("Dr. T.K.V. Iyengar, Dr. M.V.S.S.N. Prasad, S. Ranganatham & Dr. B. Krishna Gandhi")
        ['Dr. T.K.V. Iyengar', 'Dr. M.V.S.S.N. Prasad', 'S. Ranganatham', 'Dr. B. Krishna Gandhi']
        
        >>> handle_llm_authors(["Dr. T.K.V. Iyengar, Dr. M.V.S.S.N. Prasad, S. Ranganatham & Dr. B. Krishna Gandhi"])
        ['Dr. T.K.V. Iyengar', 'Dr. M.V.S.S.N. Prasad', 'S. Ranganatham', 'Dr. B. Krishna Gandhi']
    """
    # 如果输入是列表但只有一个元素，则提取第一个元素处理
    if isinstance(authors, list):
        if len(authors) == 1:
            authors = authors[0]
        elif len(authors) > 1:
            # 如果已经是多个元素的列表，直接返回
            return authors
    
    # 现在 authors 应该是字符串
    if not isinstance(authors, str):
        return []
    
    # 清理各种空格和控制字符
    authors = authors.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
    authors = authors.replace('\u00a0', ' ').replace('\u2009', ' ').replace('\u200b', '').replace('\ufeff', '')
    
    # 合并多个空格为单个空格
    authors = re.sub(r'\s+', ' ', authors).strip()
    
    # 使用逗号和 & 符号分割作者
    # 先按逗号分割，然后再按 & 符号分割
    author_list = []
    parts = authors.split(', ')
    for part in parts:
        if ' & ' in part:
            # 如果部分中包含 & 符号，则进一步分割
            sub_parts = part.split(' & ')
            author_list.extend([author.strip() for author in sub_parts])
        else:
            author_list.append(part.strip())
    
    # 过滤掉空字符串
    author_list = [author for author in author_list if author]
    
    return author_list

def clean_author_text(text: str) -> str:
    """清理作者文本中的多余空格和特殊字符"""
    if not text:
        return ""
    
    # 清理各种空格和控制字符
    text = text.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
    text = text.replace('\u00a0', ' ').replace('\u2009', ' ').replace('\u200b', '').replace('\ufeff', '')
    
    # 合并多个空格为单个空格
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text

def llm_handle_isbn (isbn: Union[str, List[str], None]) -> str:
    """
    从大模型返回的各种可能格式中提取有效的ISBN
    
    Args:
        isbn: 可能是字符串、字符串列表或者None
        
    Returns:
        str: 有效的ISBN（优先ISBN13，其次ISBN10），如果没有找到则返回空字符串
    """
    if not isbn:
        return ""
    
    # 如果是列表，转换为列表形式统一处理
    if isinstance(isbn, str):
        isbns = [isbn]
    elif isinstance(isbn, list):
        isbns = isbn
    else:
        return ""
    
    logger.info(f"[info] 正在处理 ISBN: {isbn}")
    
    # 清理并规范化所有ISBN
    normalized_isbns = []
    for i in isbns:
        # 移除非ISBN字符（保留数字和X）
        clean_isbn = re.sub(r'[^0-9Xx]', '', str(i))
        if len(clean_isbn) in [10, 13]:  # 有效ISBN长度
            normalized_isbns.append(clean_isbn)
    
    # 优先选择ISBN13
    isbn13_candidates = [i for i in normalized_isbns if len(i) == 13]
    
    # 如果有多个ISBN13，尝试验证哪个在Google Books中有结果
    if len(isbn13_candidates) > 1:
        for isbn13 in isbn13_candidates:
            if _validate_isbn_with_google_books(isbn13):
                return isbn13
        # 如果都没有结果，返回第一个
        return isbn13_candidates[0] if isbn13_candidates else ""
    
    # 如果只有一个ISBN13，直接返回
    if len(isbn13_candidates) == 1:
        # 验证有效性，如果无效则尝试找其他选项
        if _validate_isbn_with_google_books(isbn13_candidates[0]):
            return isbn13_candidates[0]
        # 如果无效，继续查找其他可能的有效ISBN
    
    # 如果有单个ISBN13但未通过验证，也返回它（因为可能API暂时不可用）
    if isbn13_candidates:
        return isbn13_candidates[0]
    
    # 如果没有有效的ISBN13，尝试使用ISBN10
    isbn10_candidates = [i for i in normalized_isbns if len(i) == 10]
    if isbn10_candidates:
        # 验证ISBN10有效性
        # for isbn10 in isbn10_candidates:
        #     if _validate_isbn_with_google_books(isbn10):
        #         return isbn10
        # 如果都没有结果，返回第一个ISBN10
        return isbn10_candidates[0]
    
    return ""

# 新增：从文本中用本地 Ollama 推断 edition（仅返回数字或空）
def llm_handle_edition(text):
    """
    将 title/subtitle/description 发给本地 Ollama，尝试提取版本号（例如 'seventh edition' -> 返回 '7'）。
    返回数字字符串（例如 '7'）或空字符串。
    """
    if not text.strip():
        return ""
    prompt = (
        "请从下面的书籍元信息文本中判断该书是否提到“第几版”或“第 N 版”（例如 'seventh edition'、'7th edition' 等）。"
        " 如果能识别出版本，请仅返回数字，例如 7；如果无法确定或未提及，请仅返回空字符串。\n\n"
        f"文本：\n{text}\n\n只返回例如 '7' 或 ''，不要其他说明。"
    )
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0}
    }
    try:
        resp = requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload, timeout=120)
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
        # 优先用数字
        m = re.search(r"(\d+)", raw)
        if m:
            return m.group(1)
        # 若没有数字，尝试常见序数词映射
        s = raw.lower()
        ord_map = {
            "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
            "sixth": "6", "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10"
        }
        for word, num in ord_map.items():
            if word in s:
                return num
        return ""
    except Exception as e:
        logger.warn(f"LLM 解析 edition 失败: {e}")
        return ""

# -------------- Google Books 补数据 --------------
# 默认不带版本 edition，需要自己通过其他方式获取
def parse_metadata_gogl(isbn=None):
    """
    调用 Google Books API，仅返回核心字段，键名与官方保持一致。
    参数优先使用 ISBN，不使用书名，不准确
    """
    try:
        # 构造查询 URL
        if isbn:
            url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{requests.utils.quote(str(isbn))}"
        else:
            return {}

        rsp = requests.get(url, timeout=20, proxies=PROXIES)
        rsp.raise_for_status()
        data = rsp.json()

        if data.get("totalItems", 0) == 0 or "items" not in data:
            return {}

        vol = data["items"][0]          # 取第一条记录
        vi  = vol.get("volumeInfo", {})

        # 只提取你需要的字段，保持原名
        result = {
            "title"            : vi.get("title"),
            "subtitle"         : vi.get("subtitle"),
            "authors"          : vi.get("authors"),
            "publisher"        : vi.get("publisher"),
            "publishedDate"    : vi.get("publishedDate"),
            "description"      : vi.get("description"),
            "categories"       : vi.get("categories"),
            #"imageLinks"       : vi.get("imageLinks"),
            "language"         : vi.get("language"),
            #"industryIdentifiers": vi.get("industryIdentifiers"),
            "id"               : vol.get("id")   # Google Books 唯一 ID
        }
            
        # 处理ISBN
        # "industryIdentifiers": [
        #   {
        #     "type": "ISBN_10",
        #     "identifier": "935260640X"
        #   },
        #   {
        #     "type": "ISBN_13",
        #     "identifier": "9789352606405"
        #   }
        # ]
        isbns=vi.get("industryIdentifiers","")
        if isbns:
            for identifier in vi.get("industryIdentifiers"):
                if identifier.get("type") == "ISBN_13":
                    result["isbn"] = identifier.get("identifier")
                    break
                      
        # 处理imagelink
        cover_images = vi.get("imageLinks", "")
        if cover_images:
            result["imageLink"] = cover_images.get("thumbnail","")
        

        # 去掉值为 None 的键，保持干净
        return {k: v for k, v in result.items() if v is not None}

    except Exception as e:
        logger.warn("[warn] Google Books 抓取失败:", e)
        return {}

def parse_metadata(text, pdf_path):
    """
    解析元数据的主要函数，包含所有后处理逻辑
    1、先通过本地大模型获取 isbn
    2、通过 isbn 从 Google Books 中获取元数据
    3、优先使用 Google 数据；如果 Google Books 没有返回数据，则使用本地大模型补数据，如 edition 等
    """
    
    """
    {
        "id": "2EVIzwEACAAJ",  ## 或者 NLJR-xxxxxxxxxxxx
        "isbn": "9789351341574",
        "isbn_type": "ISBN_13",
        "title": "Training Manual for Industrial Training Institutues Pt. 1",
        "subtitle": "Unlocking the Power of AI and Human Insight in Effective Deal-Making",
        "authors": [
            "Keld Jensen"
        ],
        "pageCount": 271,
        "publishedDate": "2014-05-04",
        "categories": [
            "Computer Science"
        ],
        "imageLinks": "http://books.google.com/books/content?id=vphxEQAAQBAJ&printsec=frontcover&img=1&zoom=1&edge=curl&source=gbs_api",
        "title_zh": "工业培训机构培训手册 第一部分",
        "description_zh": "",
        "language": "en",
        "filename": "Training Manual for Industrial Training Institutues Pt. 1 (2014) - 工业培训机构培训手册 第一部分.pdf",
        "filenameMD5": "5d41402abc4b2a76b9719d911017c592",
        "llm_categories": []
    }
    """
    
    meta = {    
        "id": random_id_12(),  ## 或者 NLJR-xxxxxxxxxxxx
        "isbn": "",
        "isbn_type": "ISBN_13",
        "title": "",
        "subtitle": "",
        "authors": [],
        "pageCount": 1,
        "publishedDate": "1995-10-20",
        "categories": [],
        "imageLink": "",
        "title_zh": "",
        "description_zh": "",
        "language": "",
        "filename": "",
        "filenameMD5": "",
        "llm_categories": []
    }
    
    llm_meta = parse_metadata_llm(text, meta)
    # Google Books 补数据
    gogl_meta = parse_metadata_gogl(llm_meta.get("isbn"))
    # 合并两个元数据源，优先使用Google Books的数据
    # 先复制llm_meta的所有数据到meta
    for key, value in llm_meta.items():
        meta[key] = value
    # 然后用gogl_meta的数据覆盖相同键的值（优先使用Google Books数据）
    for key, value in gogl_meta.items():
        if value is not None:  # 只有当gogl_meta中的值不是None时才覆盖
            meta[key] = value
    # 保存大模型的分类结果，以便后续合并使用；大模型自身的书籍类型分析，还比较准的
    meta["llm_categories"] = llm_meta.get("categories", [])
    # --- 合并完成 ---
    
    # 翻译简介，如果没有则使用英文description
    description = meta.get("description","")
    if description:
        meta["description_zh"] = translate_with_llm(description) if description else ""
    
    title = meta.get("title")
    if title: # 有title才需要后续处理的部分
        # 合并title和subtitle进行翻译
        full_title = title
        if meta.get("subtitle"):
            full_title = f"{full_title}: {meta['subtitle']}"
        if description:
            custom_prompt = f"1、当前翻译内容是书籍标题；2、请参考书籍简介：{description}，要求做到信雅达。"
        else:
            custom_prompt = "当前翻译内容是书籍标题，要求做到信雅达。"
        title_zh = translate_with_deepseek_api(full_title,custom_prompt) or translate_with_llm(full_title)
        if len(title_zh) > 50:
            title_zh = translate_with_llm(full_title,"请将以下书籍名称翻译成简体中文，要求做到信雅达，仅输出译文，禁止任何解释、括号、引号、备注、说明、标点扩展：")
        else:
            meta["title_zh"] = title_zh
        
        meta["filename"] = build_filename(meta)
        meta["filenameMD5"] = get_text_md5(title)
    else:
        logger.error("缺少书籍标题，请检查大模型解析的元数据是否正确")
    
    # 设置页数为PDF实际页数
    meta["pageCount"] = get_pdf_page_count(pdf_path)
    
    isbn = meta.get("isbn", "")
    if isbn:
        isbn_len = len(isbn)
        if isbn_len == 13:
            meta["isbn_type"] = "ISBN_13"
        elif isbn_len == 10:
            meta["isbn_type"] = "ISBN_10"
        else:
            meta["isbn_type"] = "UNKNOWN"
        
    return meta

# -------------- 翻译 --------------
def translate_with_llm(text, custom_prompt = TRANS_USR_PROMPT):
    """
    使用本地 Ollama 模型进行翻译
    """
    if not text or not text.strip():
        return text
        
    prompt = f"{custom_prompt}\n\n{text}"
    #logger.info(翻译 ➜", prompt)
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1
        }
    }
    try:
        resp = requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload, timeout=300)
        resp.raise_for_status()
        result = resp.json()["response"].strip()
        return result
    except Exception as e:
        logger.warn(f"LLM翻译失败: {e}")
        return text

def translate_with_deepseek_api(text:str,extra_prompt = ""):
    """
    使用 DeepSeek API 进行翻译
    """
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": "deepseek-reasoner",
        "messages": [
            {"role": "system", "content": TRANS_SYS_PROMPT},
            {"role": "user", "content": extra_prompt + TRANS_USR_PROMPT + "\n\n" + text}
        ],
        "temperature": 0.3
    }
    #logger.info(翻译 ➜", data)
    try:
        response = requests.post("https://api.deepseek.com/v1/chat/completions", headers=headers, json=data, timeout=60)
        response.raise_for_status()
        #logger.info(翻译 ➜", response.json())
        result = response.json()["choices"][0]["message"]["content"]
        return result
    except Exception as e:
        logger.error(f"翻译失败: {e}")

def save_meta_json(meta) -> str:
    """
    将元数据保存为JSON文件
    """
    safe_title = get_safe_title(meta)
    
    if not safe_title:
        return ""
    
    # 修改JSON文件名为与MD文件名一致的格式
    json_path = os.path.join(META_DIR, f"{safe_title}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    logger.info("已生成元数据文件" + os.path.basename(json_path))    
    return json_path

def merge_categories(meta: Dict[str, object]) -> str:
    """
    合并 categories 和 lll_categories，去除重复项并返回字符串
    """
    categories = meta.get("categories", [])
    llm_categories = meta.get("llm_categories", [])
    
    # 确保都是列表形式
    if isinstance(categories, str):
        categories = [categories]
    elif categories is None:
        categories = []
        
    if isinstance(llm_categories, str):
        llm_categories = [llm_categories]
    elif llm_categories is None:
        llm_categories = []
    
    # 合并并去重，保持顺序
    merged = list(dict.fromkeys(categories + llm_categories))
    
    # 返回字符串格式
    return ", ".join(merged) if merged else "无"

# -------------- 工具函数 --------------

import secrets, string
# 如果没有GoogleID，生成12位随机ID
def random_id_12() -> str:
    """12 位 *URL-safe* 随机 ID（数字 + 大小写字母）"""
    alphabet = string.ascii_letters + string.digits  # abc...ABC...012...9
    return "NLJR-"+''.join(secrets.choice(alphabet) for _ in range(12))

def _validate_isbn_with_google_books(isbn: str) -> bool:
    """
    使用Google Books API验证ISBN是否有效
    
    Args:
        isbn: ISBN字符串
        
    Returns:
        bool: 如果ISBN在Google Books中存在返回True，否则返回False
    """
    try:
        url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
            
        response = requests.get(url, timeout=10, proxies=PROXIES)
        response.raise_for_status()
        data = response.json()
        return data.get("totalItems", 0) > 0
    except:
        return False


def build_filename(meta: Dict[str, object]) -> str:
    """
    根据元数据生成文件名:
    [title] ([year])[, [edition]E] - [title_zh].pdf
    当 edition 为 1 或缺失时不出现版次片段。
    """
    # 1. 处理标题（英文）
    title = meta.get("title", "Untitled").strip()
    title = re.sub(r'[\\/:*?"<>|]', '', title)

    # 2. 年份
    pub_date = meta.get("publishedDate", "")
    year_match = re.search(r'\d{4}', pub_date)
    year = year_match.group() if year_match else "0000"

    # 3. 版次（仅当 >1 时才出现）
    edition_part = ""
    edition = meta.get("edition")
    if edition:
        try:
            edition_int = int(edition)
            if edition_int > 1:
                edition_part = f", {edition_int}E"
        except (ValueError, TypeError):
            pass

    # 4. 中文译名
    title_zh = meta.get("title_zh", "").strip()
    #logger.debug(f"[info] 重要，拼接所用标题: {title_zh}")
    title_zh = re.sub(r'[\\/:*?"<>|]', '', title_zh)

    # 5. 拼接
    filename = f"{title} ({year}){edition_part} - {title_zh}.pdf"
    
    if len(filename) > MAX_FILENAME_LENGTH:
        logger.warn(f"文件名过长，尝试通过逻辑处理")
        filename = shorten_filename_by_segments(title, title_zh, year, edition_part)
        logger.info(f"短文件名: {filename}")
    
    if filename.lstrip().startswith('-') or filename.lstrip().endswith('-'):
       return ""
   
    logger.info("已生成有效文件名：" + filename)
    return filename


def shorten_filename_by_segments(title: str, title_zh: str, year: str, edition_part: str, 
                                max_length: int = MAX_FILENAME_LENGTH) -> str:
    """
    通过逻辑拆分标题来处理文件名超长问题
    
    Args:
        title: 英文标题
        title_zh: 中文标题
        year: 年份信息
        edition_part: 版本信息
        max_length: 最大文件名长度
        
    Returns:
        str: 缩短后的文件名
    """
    # 年份和版本部分
    year_edition_part = f" ({year}){edition_part}" if year else f"{edition_part}"
    
    # 分割标题为片段
    # 使用破折号和分号作为分割符
    title_segments = re.split(r'[–—\-－―﹣－—]', title)  # 包含各种破折号
    title_zh_segments = re.split(r'[–—\-－―﹣－—；;]', title_zh)  # 包含各种破折号和分号
    
    # 清理每段内容，移除首尾空格
    title_segments = [seg.strip() for seg in title_segments if seg.strip()]
    title_zh_segments = [seg.strip() for seg in title_zh_segments if seg.strip()]
    
    # 如果没有分割符，则将整个标题作为一个段落
    if not title_segments:
        title_segments = [title]
    if not title_zh_segments:
        title_zh_segments = [title_zh]
    
    # 尝试不同的组合方案
    # 方案1: 英文一段 + 中文两段
    if len(title_segments) >= 1 and len(title_zh_segments) >= 2:
        candidate = f"{title_segments[0]}{year_edition_part} - {title_zh_segments[0]}；{title_zh_segments[1]}.pdf"
        if len(candidate) <= max_length:
            return candidate
    
    # 方案2: 英文一段 + 中文一段
    if len(title_segments) >= 1 and len(title_zh_segments) >= 1:
        candidate = f"{title_segments[0]}{year_edition_part} - {title_zh_segments[0]}.pdf"
        if len(candidate) <= max_length:
            return candidate
    
    # 方案3: 只保留英文标题第一段
    if len(title_segments) >= 1:
        candidate = f"{title_segments[0]}{year_edition_part}.pdf"
        if len(candidate) <= max_length:
            return candidate
    
    # 如果以上方案都不行，强制截断英文标题
    # 保证基本结构: 英文标题(至少保留10个字符) + 年份 + 扩展名(.pdf = 4字符)
    min_required = len(year_edition_part) + 4 + 10  # 至少保留10个字符的标题
    if max_length > min_required:
        max_title_len = max_length - len(year_edition_part) - 4
        truncated_title = title_segments[0][:max_title_len] if title_segments else title[:max_title_len]
        return f"{truncated_title}{year_edition_part}.pdf"
    
    # 最坏情况下，只保留年份和扩展名
    return f"Book{year_edition_part}.pdf"

# -------------- 主流程 --------------
def cip_parser(pdf_path: str, processed_info: Dict[str, Any]) -> Dict:    
    logger.info(f"正在定位版权页[ {pdf_path} ] …")
    text = find_copyright_page(pdf_path)
    # log(f"[debug] 版权页内容: {text}")
    if not text:
        logger.warn("未找到版权页")
        return
    
    meta = parse_metadata(text, pdf_path)
    
    if not meta.get("title"):
        logger.warn("未找到标题，尝试判断是否有加密文本，解密后再解析")
        decrypt_text = detect_and_decrypt_mixed_text(text)
        if decrypt_text and decrypt_text != text:
            meta = parse_metadata(decrypt_text, pdf_path)
            if meta.get("title"):
                logger.info(f"解析标题成功，为{meta.get("title")}")
        else:
            logger.info(f"文本未加密，找不到标题，请手动处理")
    
    json_path = save_meta_json(meta)
    if json_path:
        processed_info["status"]["parse_metadata"] = True
        processed_info["meta_json"] = os.path.basename(json_path)
        processed_info["safe_title"] = get_safe_title(meta)
    return processed_info



def detect_and_decrypt_mixed_text(text: str) -> str:
    """
    使用本地Ollama判断文本中是否有加密内容，如果有则解密并返回整体解密文本
    
    Args:
        text (str): 待检测和解密的文本
        
    Returns:
        str: 解密后的完整文本
    """
    import requests
    import json
    
    # 分割文本为段落/行
    lines = text.split('\n')
    processed_lines = []
    
    # Ollama API端点
    ollama_url = f"{OLLAMA_BASE_URL}/api/generate"
    
    for line in lines:
        # 跳过空行
        if not line.strip():
            processed_lines.append(line)
            continue
            
        # 构建提示词，询问Ollama这行文本是否包含加密内容
        prompt = f"""
        Analyze the following text and determine if it contains encrypted or obfuscated content.
        Encrypted content typically looks like random letters and symbols with no apparent meaning.
        Normal text is readable in English or other natural languages.
        
        Text: "{line}"
        
        Respond with ONLY "YES" if the text appears to be encrypted, or "NO" if it's normal readable text.
        """
        
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False
        }
        
        try:
            # 发送请求到Ollama
            response = requests.post(ollama_url, json=payload, timeout=300)
            response.raise_for_status()
            
            result = response.json()
            answer = result.get('response', '').strip().upper()
            
            # 如果判断为加密文本，则进行解密
            if answer.startswith("YES"):
                decrypted_line = decrypt_caesar_shift(line)
                processed_lines.append(decrypted_line)
            else:
                processed_lines.append(line)
                
        except Exception as e:
            # 如果Ollama请求失败，保留原行
            logger.warn(f"Ollama 请求失败: {e}")
            processed_lines.append(line)
    
    return '\n'.join(processed_lines)


def decrypt_caesar_shift(encrypted_text: str) -> str:
    """
    解密凯撒密码偏移加密的文本（每个字符向后偏移1位）
    
    Args:
        encrypted_text (str): 加密的文本
        
    Returns:
        str: 解密后的文本
    """
    decrypted_chars = []
    for char in encrypted_text:
        # 对于每个字符，将其ASCII值减1来解密
        try:
            decrypted_char = chr(ord(char) - 1) if ord(char) > 0 else char
            decrypted_chars.append(decrypted_char)
        except Exception:
            # 如果解密失败，保留原字符
            decrypted_chars.append(char)
    
    return ''.join(decrypted_chars)

def main(pdf_path):
    cip_parser(pdf_path)
    
   
if __name__ == "__main__":
    if len(sys.argv) != 2:
        logger.info("用法: python batch_01_cip_parser.py xxx.pdf")
        sys.exit(1)
    main(sys.argv[1])