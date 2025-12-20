# -*- coding: utf-8 -*-
"""
Ollama 工具函数模块
提供模型检查等通用功能
"""

import requests
import os
import re
import json
import inspect
import platform
from typing import Dict, Any
import fitz  # PyMuPDF
import hashlib
from pathlib import Path
from slugify import slugify
from difflib import SequenceMatcher
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from dotenv import load_dotenv

# ==============================
# 配置（需要在导入前设置这些变量）
# ==============================
# 加载 .env 文件
load_dotenv()

# 从环境变量加载这些值，如果环境变量不存在则使用默认值
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")          # Ollama 服务地址
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")       # 默认模型
DEEPSEEK_API_KEY= os.getenv("DEEPSEEK_API_KEY", "sk-xxxxxxxxxxx")
OLLAMA_TIMEOUT  = int(os.getenv("OLLAMA_TIMEOUT", "300"))
MAX_RETRIES     = int(os.getenv("MAX_RETRIES", "3"))
TARGET_SUFFIXES = os.getenv("TARGET_SUFFIXES", "_dual.pdf,_translated.pdf,_dual_智谱4Flash.pdf,_translated_智谱4Flash.pdf,_dual_Kimi+DeepSeek.pdf,_translated_Kimi+DeepSeek.pdf,_translated_Kimi+Qwen.pdf,_dual_Kimi+Qwen.pdf,.no_watermark.zh-CN.mono.pdf,.no_watermark.zh-CN.dual.pdf,_zh.pdf,_cn.pdf,_final.pdf,_bilingual.pdf").split(",")
RENAME_PDF_FILES = os.getenv("RENAME_PDF_FILES", "true").lower() == "true"  # 是否重命名PDF文件

# ==============================
# 2. 路径与日志
# ==============================
SCRIPT_DIR          = Path(__file__).parent.resolve()
WIKI_BASE_PATH: Path= Path(os.getenv("WIKI_BASE_PATH", ""))
EOOKS_PATH: Path = Path(os.getenv("EOOKS_PATH", ""))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
PROXIES = {}
http_proxy = os.getenv("HTTP_PROXY")
https_proxy = os.getenv("HTTPS_PROXY")
if http_proxy or https_proxy:
    PROXIES = {}
    if http_proxy:
        PROXIES["http"] = http_proxy
    if https_proxy:
        PROXIES["https"] = https_proxy

DATA_DIR        = SCRIPT_DIR / "data"
PROCESSING_DIR  = DATA_DIR / "processing"
LOG_DIR         = SCRIPT_DIR / "logs"
PROCESSING_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
# 根据hostname设置不同的日志文件
LOG_FILE = LOG_DIR / f"pdf_plumber_{platform.node()}.log"

# 日志系统
class Logger:
    """统一的日志类，支持不同级别日志和根据配置控制日志输出"""
    
    def __init__(self, name):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)
        
        # 避免重复添加处理器
        if not self.logger.handlers:
            # 创建格式化器，移除了%(filename)s部分，因为我们已经在消息中包含了脚本名
            formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', 
                                        datefmt='%Y-%m-%d %H:%M:%S')
            
            # 控制台处理器
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)
            
            # 文件处理器 - 使用TimedRotatingFileHandler按天轮转日志
            file_handler = TimedRotatingFileHandler(
                LOG_FILE, 
                when="midnight", 
                interval=1, 
                backupCount=30, 
                encoding='utf-8'
            )
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
        
        # 根据配置设置日志级别
        level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
        self.logger.setLevel(level)
    
    def _get_caller_script(self):
        """获取调用者的脚本名"""
        # 获取调用栈，往上追溯3层：
        # 第0层：_get_caller_script函数本身
        # 第1层：debug/info等日志方法
        # 第2层：Logger的方法（如_debug/_info等）
        # 第3层：实际调用日志的业务代码
        frame = inspect.currentframe().f_back.f_back.f_back
        script = frame.f_globals.get("__file__", "<unknown>")
        return script.split("/")[-1].split("\\")[-1]
    
    def _log_with_script(self, level, msg):
        """包装日志方法，添加脚本名前缀"""
        caller_script = self._get_caller_script()
        formatted_msg = f"[{caller_script}] {msg}"
        getattr(self.logger, level)(formatted_msg)
    
    def debug(self, msg):
        self._log_with_script('debug', msg)
    
    def info(self, msg):
        self._log_with_script('info', msg)
    
    def error(self, msg):
        self._log_with_script('error', msg)
    
    def warn(self, msg):
        self._log_with_script('warn', msg)

# 创建全局logger实例
logger = Logger("pdfplumber")

# 用于缓存模型检查结果的字典
_model_check_cache = {}

# ==============================
# 模型检查功能
# ==============================
def check_model_exists(base_url=None, model_name=None):
    """
    检查远程 Ollama 模型是否存在
    
    Args:
        base_url (str, optional): Ollama 服务地址，默认使用全局配置
        model_name (str, optional): 模型名称，默认使用全局配置
    
    Raises:
        SystemExit: 当模型不存在或连接失败时退出程序
    """
    url = f"{base_url or OLLAMA_BASE_URL}/api/tags"
    model = model_name or OLLAMA_MODEL
    
    # 创建缓存键
    cache_key = (url, model)
    
    # 如果已经检查过该模型，则直接返回缓存结果
    if cache_key in _model_check_cache:
        return _model_check_cache[cache_key]
    
    try:
        logger.info(f"正在检查远程模型是否存在：{url}")
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()

        available_models = [m["name"] for m in data.get("models", [])]
        if not available_models:
            logger.error("远程服务返回空模型列表！请确认 Ollama 已启动且有模型")
            raise SystemExit(1)

        if model not in available_models:
            logger.error(f"远程服务器未找到模型 '{model}'")
            logger.info("请在服务器执行：")
            logger.info(f"   ollama pull {model}")
            logger.info("当前已安装模型：")
            for m in available_models:
                size = next((item["size"] for item in data["models"] if item["name"] == m), "未知")
                logger.info(f"   • {m} ({size})")
            raise SystemExit(1)

        logger.info(f"模型检查通过：{model} 已就绪")
        _model_check_cache[cache_key] = True
        return True
    except requests.exceptions.ConnectionError:
        logger.error(f"无法连接 Ollama 服务 {base_url or OLLAMA_BASE_URL}")
        logger.info("请确认服务已启动并执行：OLLAMA_HOST=0.0.0.0 ollama serve")
        raise SystemExit(1)
    except Exception as e:
        logger.error(f"检查模型时出错：{e}")
        raise SystemExit(1)

def load_json_file(json_path: Path) -> Dict[Any, Any]:
    """
    读取JSON文件并返回其内容
    
    Args:
        json_path (Path): JSON文件的路径
        
    Returns:
        dict: JSON文件的内容
        
    Raises:
        FileNotFoundError: 当文件不存在时
        json.JSONDecodeError: 当文件不是有效的JSON格式时
    """
    if not json_path.exists():
        raise FileNotFoundError(f"JSON文件不存在: {json_path}")
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"JSON文件格式错误: {json_path}", e.doc, e.pos)

def is_target_file(filename: str) -> bool:
    """判断文件是否为目标文件（即需要被忽略的文件）
    
    Args:
        filename (str): 文件名
        
    Returns:
        bool: 如果是目标文件返回True，否则返回False
    """
    for suffix in TARGET_SUFFIXES:
        if filename.endswith(suffix):
            return True
    return False

def is_target_file_2(filename: str) -> str:
    """判断文件是否为目标文件（即需要被忽略的文件）
    
    Args:
        filename: 文件名
        
    Returns:
        str: 返回特定后缀
    """
    for suffix in TARGET_SUFFIXES:
        if filename.endswith(suffix):
            return suffix
    return ""

def get_safe_title(meta) -> str:
    safe_title= slugify(meta.get("title", "")) + "-" + meta.get("id", "").lower()
    if safe_title.startswith("-") or safe_title.endswith("-"):
        logger.debug(f"获取到 safe_title 有问题: {safe_title}")
        return ""
    return safe_title

def get_pdf_page_count(pdf_path):
    """
    获取PDF文件的总页数
    """
    doc = fitz.open(pdf_path)
    page_count = doc.page_count
    doc.close()
    return page_count

def get_text_md5(text: str) -> str:
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def is_highly_similar(text1, text2, threshold=0.99):
    """
    判断两段文本是否相同或相似度≥99%
    返回: True/False
    """
    # 完全相同直接返回True
    if text1 == text2:
        return True
    
    # 计算相似度
    similarity = SequenceMatcher(None, text1, text2).ratio()
    return similarity >= threshold

def has_bookmarks(pdf_path: Path) -> bool:
    with fitz.open(pdf_path) as doc:
        # 大纲（Outline）即书签；无大纲时返回空列表
        return bool(doc.get_toc())   # True → 有书签，False → 无书签

# 规范化 ISBN 方便比较
def _normalize_isbn(s):
    return re.sub(r'[^0-9Xx]', '', s or '')

def has_explanatory_note(src: str, trans: str) -> bool:
    """
    判断翻译内容是否带有解释性说明。
    如果包含常见的解释性短语（如“根据上下文”、“注：”、“建议”、“应当直接翻译为”等），返回 True。
    
    参数:
        src (str): 原文
        trans (str): 译文
    
    返回:
        bool: 有解释性说明返回 True，否则 False
    """
    # 有多行内容，说明有注释
    # 如：【仪表礼仪 для成功
    #
    # 注：根据上下文和指导原则，“Dressing for success”更贴切的翻译应为“仪表礼仪”，而非直接翻译为“着装以求成功”。因此，此处无需额外补充说明。】
    if '\n' not in src and '\n' in trans:
        logger.debug(f"翻译有换行：{trans}")
        return True

    # 先快速正则匹配
    if has_explanatory_note_re(trans.replace('\n', ' ')):
        return has_explained_content_llm  # LLM 判断
 
    return False
    
def has_explanatory_note_re(text: str) -> bool:
    """
    通过正则表达式判断翻译内容是否带有解释性说明。
    如果包含常见的解释性短语（如“根据上下文”、“注：”、“建议”、“应当直接翻译为”等），返回 True。
    
    参数:
        text (str): 需要检查的翻译字符串
    
    返回:
        bool: 有解释性说明返回 True，否则 False
    """
    if not text:
        return False
    
    # 常见的解释性关键词和模式（可根据实际日志扩展）
    patterns = [
        r'译',
        # r'译文',
        # r'翻译',
        # r'译为',
        r'原文',
        r'建议',
        r'根据',
        r'纠正',
        # r'应译为',
        # r'建议.*翻译为',
        # r'应翻译为',
        # r'应当翻译为.*或',
        # r'直接翻译',
        r'注：',                 # 注释开头
        r'备注：',
        # r'建议根据',
        r'上下文',          # 如“根据上下文\依据上下文”
        r'无需.*说明',
        r'若未明确指示',
        r'按照.*指导',
        r'如需',
        r'不必考虑',
        r'简化为',
        # r'如原文中出现',
    ]
    
    # 合并为一个大正则
    combined_pattern = '|'.join(patterns)
    
    return bool(re.search(combined_pattern, text))

def has_explained_content_llm(src: str, mt: str) -> bool:
    """
    比较两段文本：
    src 是原文；
    mt 是译文。
    返回 True 表示 mt 里**多出了**解释性内容，False 表示没有。
    """
    prompt = (
        "你是一名严格的翻译质检员。下面给你两段中文：\n"
        "第一段【原文】是标准答案，不含任何解释、注释、说明、理由等；\n"
        "第二段【译文】是待检查版本。\n"
        "请判断【译文】是否**额外出现**了解释、注释、说明、理由、补充等“解释性内容”。\n"
        "注意：不能仅凭几个词就下结论，要综合语义判断；如果【译文】只是用词不同而没有额外解释，请返回 false。\n"
        "请只回答 true 或 false，不要输出任何额外文字。\n\n"
        f"【原文】\n{src}\n\n"
        f"【译文】\n{mt}"
    )

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "temperature": 0
    }

    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
            timeout=OLLAMA_TIMEOUT
        )
        resp.raise_for_status()
        answer = resp.json()["response"].strip().lower()
        return answer == "true"
    except Exception as e:
        logger.warn(f"Ollama call failed: {e}")
        return False

# ==============================
# 4. 深度清理标题（重点！解决 &#10; 换行问题）
# ==============================
def deep_clean_title(text: str) -> str:
    """彻底清除 PDF 书签中的换行符、控制字符、XML 实体"""
    if not text:
        return ""
    # XML 实体换行
    text = re.sub(r'&#(?:x0?[0A9D]|10|13);', ' ', text)
    # 真实换行
    text = text.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
    # 各种空格
    text = text.replace('\u00a0', ' ').replace('\u2009', ' ').replace('\u200b', '').replace('\ufeff', '')
    # 非法字符
    text = re.sub(r'[\ud800-\udfff]', '', text)
    # 合并空格
    text = re.sub(r'\s+', ' ', text).strip()
    return text