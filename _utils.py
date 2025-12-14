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
from datetime import datetime
from typing import Dict, Any
import fitz  # PyMuPDF
import hashlib
from pathlib import Path
from slugify import slugify
from difflib import SequenceMatcher
import logging
import sys


# ==============================
# 配置（需要在导入前设置这些变量）
# ==============================
# 从配置文件加载这些值，如果配置文件不存在则使用默认值
OLLAMA_BASE_URL = "http://localhost:11434"          # Ollama 服务地址
OLLAMA_MODEL    = "qwen2.5:7b"       # 默认模型
DEEPSEEK_API_KEY= ""
OLLAMA_TIMEOUT  = 300
MAX_RETRIES     = 3
TARGET_SUFFIXES = [
        '_dual.pdf', '_translated.pdf', '_dual_智谱4Flash.pdf', '_translated_智谱4Flash.pdf',
        '_dual_Kimi+DeepSeek.pdf', '_translated_Kimi+DeepSeek.pdf', '_translated_Kimi+Qwen.pdf',
        '_dual_Kimi+Qwen.pdf', '.no_watermark.zh-CN.mono.pdf', '.no_watermark.zh-CN.dual.pdf',
        '_zh.pdf', '_cn.pdf', '_final.pdf', '_bilingual.pdf'
    ]
PROXIES={}

# ==============================
# 2. 路径与日志
# ==============================
SCRIPT_DIR      = Path(__file__).parent.resolve()
CONFIG_FILE      = SCRIPT_DIR / "config.json"

WIKI_BASE_PATH  = ""
# 加载环境相关的配置
def load_environment_config():
    """根据主机名或环境变量加载相应环境的配置"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                all_configs = json.load(f)
            
            # 获取当前环境标识（优先级：环境变量 > 主机名）
            env_name = os.environ.get('APP_ENV') or platform.node()
            
            # 查找匹配的环境配置
            config = {}
            for key, value in all_configs.items():
                if key == env_name or (isinstance(value, dict) and value.get('hostname') == env_name):
                    config = value
                    break
            
            # 如果没找到精确匹配，尝试模糊匹配主机名
            if not config:
                for key, value in all_configs.items():
                    if isinstance(value, dict) and 'hostname' in value and value['hostname'] in env_name:
                        config = value
                        break
            
            # 如果仍然没找到，使用默认配置（如果有）
            if not config and 'default' in all_configs:
                config = all_configs['default']
                
            return config
        except Exception as e:
            logger.debug(f"加载配置文件时出错: {e}")
    return {}

# 应用环境配置
env_config = load_environment_config()
WIKI_BASE_PATH = env_config.get("WIKI_BASE_PATH", WIKI_BASE_PATH)

LOG_LEVEL = env_config.get("LOG_LEVEL", "INFO")

# 加载配置信息
OLLAMA_BASE_URL = env_config.get("OLLAMA_BASE_URL", OLLAMA_BASE_URL)
OLLAMA_MODEL = env_config.get("OLLAMA_MODEL", OLLAMA_MODEL)
DEEPSEEK_API_KEY = env_config.get("DEEPSEEK_API_KEY", DEEPSEEK_API_KEY)
PROXIES = env_config.get("PROXIES", PROXIES)

DATA_DIR        = SCRIPT_DIR / "data"
PROCESSING_DIR  = DATA_DIR / "processing"
PROCESSING_DIR.mkdir(exist_ok=True)
LOG_DIR         = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
# 根据hostname设置不同的日志文件
hostname = platform.node()
LOG_FILE = LOG_DIR / f"pdf_plumber_{hostname}.log"

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
            
            # 文件处理器
            file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
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

def load_json_file(json_path: str) -> Dict[Any, Any]:
    """
    读取JSON文件并返回其内容
    
    Args:
        json_path (str): JSON文件的路径
        
    Returns:
        dict: JSON文件的内容
        
    Raises:
        FileNotFoundError: 当文件不存在时
        json.JSONDecodeError: 当文件不是有效的JSON格式时
    """
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"JSON文件不存在: {json_path}")
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"JSON文件格式错误: {json_path}", e.doc, e.pos)

def is_target_file(filename: str) -> bool:
    """判断文件是否为目标文件（即需要被忽略的文件）
    
    Args:
        filename: 文件名
        
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
        bool: 如果是目标文件返回True，否则返回False
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

def has_bookmarks(pdf_path: str) -> bool:
    with fitz.open(pdf_path) as doc:
        # 大纲（Outline）即书签；无大纲时返回空列表
        return bool(doc.get_toc())   # True → 有书签，False → 无书签


# 规范化 ISBN 方便比较
def _normalize_isbn(s):
    return re.sub(r'[^0-9Xx]', '', s or '')