# -*- coding: utf-8 -*-
"""
PDF 目录（TOC）专业翻译脚本 —— 完全本地/远程 Ollama 版
功能如下：
    • 远程模型存在性检查（支持局域网服务器）
    • 深度清理 &#10; / &#13; / \n 等换行符
    • 生成专业翻译指导（只一次）
    • 逐行翻译 + 实时调试日志 + 结果预览
    • 严格的 Part/Chapter/Appendix 格式统一
    • 行数永远对齐，永不拆行
"""

import os
import fitz
import requests
import time
import re
import shutil
import json
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List
from pathlib import Path
import xml.etree.ElementTree as ET
from xml.dom import minidom
from difflib import SequenceMatcher
# 导入公共模型检查工具
from _utils import logger, check_model_exists, is_highly_similar,is_target_file,is_target_file_2
from _utils import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT,TARGET_SUFFIXES, WIKI_BASE_PATH, MAX_RETRIES, PROCESSING_DIR, LOG_FILE

# ==============================
# 1. 基础配置（请务必检查这几行）
# ==============================
MAX_RETRIES     = 3
# 启动前强制检查模型（远程版）
check_model_exists(OLLAMA_BASE_URL, OLLAMA_MODEL)

TOC_DIR = os.path.join(WIKI_BASE_PATH, "toc")
HOURS_RECENT    =  2400
BACKUP_TARGET   =  False
SCRIPT_DIR      = Path(__file__).parent.resolve()

# ==============================
# 2. 提示词
# ==============================

TRANS_GUIDE_SYS_PROMPT = "You are a professional translation consultant specializing in providing guidance for translating technical book tables of contents. "
TRANS_GUIDE_SYS_PROMPT_ZH = "你是一个专业的翻译顾问，专门为技术书籍目录翻译提供指导。"
TRANS_GUIDE_USR_PROMPT = """Analyze the following book table of contents structure and create a professional, accurate translation guideline to ensure precise translation of technical terms and consistent style.
        
    """
TRANS_GUIDE_USR_PROMPT_ZH = """
    请分析以下书籍的书签/目录结构，并创建一个专业的翻译指导提示词：

        目录样本：
        {contents}

        要求：
        1. 识别书籍的技术领域和主题
        2. 分析目录的结构层次
        3. 提供专业的技术术语翻译建议
        4. 确保翻译风格一致性
        5. 考虑中文阅读习惯
        请返回一个完整的翻译提示词，可以直接用于指导AI进行专业翻译。只返回提示词文本的主体内容，禁止任何前言、结尾或额外说明

    """
TRANS_SYS_PROMPT = "Only return the Chinese translation, no explanations, no notes, no parentheses."
TRANS_SYS_PROMPT_ZH = "请翻译这个目录标题，仅输出译文，禁止任何解释"
TRANS_USR_PROMPT = """
    You are a professional technical book translator whose task is to translate the English table of contents into Simplified Chinese.
    You follow the translation instructions provided by the professional technical book translation reviewer; the translation guidance for this book is:

    {guide}

    In addition to the above guidance, you must also strictly comply with the following translation rules (all must be observed; any violation will be severely penalized):
    1.Output translation only — no parentheses, footnotes, endnotes, explanations, or comments.
    2.Use the fixed glossary below; do not vary:
        Preface → 前言
        Foreword → 序
        Section → 节
        Appendix → 附录
        Index → 索引
        Bibliography → 参考文献
        Conventions → 约定
        FAQ → 常见问题
        Troubleshooting → 故障排查
        Best Practices → 最佳实践
        Quick Start → 快速入门
        Hands-On → 实战
        Walkthrough → 分步指南
        Performance Tuning → 性能调优
        Security Considerations → 安全事项
    3.Keep original numbers, punctuation, and indentation; maintain source capitalization.
    4.Style: concise, technical, zero marketing fluff.
    5.Do not echo these instructions or any meta text.
    6.If the index number {index} is less than 4, translate “Introduction” as “引言”; in all other cases, translate “Introduction” as “介绍.”
    7.Again, please only return the Chinese translation, no explanations, no notes, no parentheses.
    """
TRANS_USR_PROMPT_ZH = """
    你是一名专业技术图书翻译，任务是把英文目录翻译成简体中文。
    你接受专业技术图书译审的翻译指导，对本书的这是翻译指导是：
    
    {guide}
    
    除了上述翻译指导以外，你还必须遵守以下翻译规则（必须全部遵守，违者重罚）：
        1. 仅返回译文，禁止出现任何括号、脚注、尾注、解释、说明。
        2. 使用统一术语表，不得同词异译：
            Preface → 前言
            Foreword → 序
            Appendix → 附录
            Index → 索引
            Bibliography → 参考文献
            Conventions → 约定
            FAQ → 常见问题
            Troubleshooting → 故障排查
            Best Practices → 最佳实践
            Quick Start → 快速入门
            Hands-On → 实战
            Walkthrough → 分步指南
            Performance Tuning → 性能调优
            Security Considerations → 安全事项
        3. 保留原始数字、标点和层级缩进；大小写与原版一致。
        4. 译文风格：简洁、无口语、无广告形容词。
        5. 禁止输出本条规则或任何元信息。
        6. 如果 序号 {index} 小于 4，请将 Introduction 翻译为“引言”，其他情况下，请将 Introduction 翻译成“介绍”。
        7. 再强调一遍，只输出译文，禁止任何解释。
    """


# ==============================
# 3. Ollama 调用封装
# ==============================
def ollama_chat(messages: List[dict], temp: float = 0.1) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temp, "num_predict": -1},
        #"keep_alive": "-1m"          # 显式告诉 serve 别卸载
    }
    for i in range(MAX_RETRIES):
        try:
            r = requests.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT)
            r.raise_for_status()
            return r.json()["message"]["content"]
        except Exception as e:
            logger.error(f"Ollama 调用失败({i+1}/{MAX_RETRIES}): {e}")
            if i < MAX_RETRIES - 1:
                time.sleep(3)
    return ""

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

# ===================================================================
# 5. 新增：序号固定 + 标题翻译拆分逻辑（插入到原文件任意位置即可）
# ===================================================================

# ---------------- 正则 ----------------
RE_PART   = re.compile(r'^Part\s+([0-9]+)\s*[:\-–—]\s*(.+)', re.I)
RE_CHAP   = re.compile(r'^Chapter\s+([0-9]+)\s*[:\-–—]\s*(.+)', re.I)
RE_APPEND = re.compile(r'^Appendix\s+([0-9A-Z]+)\s*[:\-–—]\s*(.+)', re.I)
RE_SECT   = re.compile(r'^Section\s+([0-9]+(?:\.[0-9]+)?)\s*[:\-–—]\s*(.+)', re.I)

# ---------------- 数字->中文 ----------------
CN_NUM = '零一二三四五六七八九十'
def _to_cn(n: int) -> str:
    if n <= 10:
        return CN_NUM[n]
    tens, units = divmod(n, 10)
    return CN_NUM[tens] + '十' + (CN_NUM[units] if units else '')

# ---------------- 格式化前缀 ----------------
def _fmt_prefix(typ: str, num: str) -> str:
    if typ == 'part':
        return f'第{_to_cn(int(num))}部分'
    if typ == 'chapter':
        return f'第 {int(num):02d} 章'
    if typ == 'appendix':
        return f'附录 {num}'
    if typ == 'section':
        return f'第 {num} 节'
    return ''

# ---------------- 剥离模型可能乱加的前缀 ----------------
def _strip_model_prefix(text: str) -> str:
    text = re.sub(r'^第\s*[0-9]+\s*章[：:]\s*', '', text)
    text = re.sub(r'^第[一二两三四五六七八九十]+部分[：:]\s*', '', text)
    text = re.sub(r'^附录\s*[0-9A-Z]+[：:]\s*', '', text)
    text = re.sub(r'^第\s*[0-9]+(?:\.[0-9]+)?\s*节[：:]\s*', '', text)
    return text.strip()

# ---------------- 核心：拆分 ----------------
def _split_line(line: str):
    """返回 (prefix_type, prefix_num, pure_title) 三元组；无前缀则返回 (None, None, line)"""
    m = RE_CHAP.fullmatch(line) or RE_PART.fullmatch(line) or RE_APPEND.fullmatch(line) or RE_SECT.fullmatch(line)
    if m:
        return ('chapter' if RE_CHAP.fullmatch(line) else
                'part'    if RE_PART.fullmatch(line) else
                'appendix'if RE_APPEND.fullmatch(line) else 'section', m.group(1), m.group(2).strip())
    return None, None, line.strip()

# ==============================
# 6. 生成专业翻译指导（只执行一次）
# ==============================
def generate_translation_guide(content_file: str, guide_file: str):
    if os.path.exists(guide_file):
        logger.info(f"翻译指导已存在，跳过生成 → {Path(guide_file).name}")
        with open(guide_file, "r", encoding="utf-8") as f:
            return f.read()

    logger.info("正在生成专业翻译指导（只需一次，稍慢正常）...")
    with open(content_file, "r", encoding="utf-8") as f:
        sample = "\n".join([l.strip() for l in f.readlines()[:120]])

    guide = ollama_chat([
        {"role": "system", "content": TRANS_GUIDE_SYS_PROMPT},
        {"role": "user", "content": TRANS_GUIDE_USR_PROMPT_ZH.format(contents=sample)}
    ], temp=0.3)

    with open(guide_file, "w", encoding="utf-8") as f:
        f.write(guide.strip() + "\n")
    logger.info("专业翻译指导生成完成！")
    return guide.strip()

# ==============================
# 7. 逐行翻译（最稳方案）
# ==============================
def translate_line_by_line(lines: List[str], trans_guide: str, trans_file: str) -> List[str]:
    total = len(lines)
    results = [""] * total

    # 恢复已有翻译（如果存在）
    if os.path.exists(trans_file):
        with open(trans_file, "r", encoding="utf-8") as f:
            existed = [l.rstrip() for l in f]
        for i, t in enumerate(existed):
            if i < total and t:
                results[i] = t
        logger.info(f"恢复已有翻译 {sum(1 for x in results if x)}/{total} 行")

    # 开始逐行处理
    for i in range(total):
        if results[i]:
            continue
        eng = lines[i].strip()

        typ, num, pure_eng = _split_line(eng)
        if typ is None:
            # 无序号，整句给模型
            trans = ollama_chat([
                {"role": "system", "content": TRANS_SYS_PROMPT},
                {"role": "user", "content": f"{TRANS_USR_PROMPT.format(guide=trans_guide, index=str(i))}。\n请翻译这个目录标题，仅输出译文，禁止任何解释：\n\n{eng}"}
            ], temp=0.01)
            results[i] = deep_clean_title(trans.strip() or eng)
        else:
            # 先让模型只翻译“纯标题”
            trans = ollama_chat([
                {"role": "system", "content": TRANS_SYS_PROMPT},
                {"role": "user", "content": f"{TRANS_USR_PROMPT.format(guide=trans_guide, index=str(i))}。\n请翻译这个目录标题，仅输出译文，禁止任何解释：\n\n{pure_eng}"}
            ], temp=0.01)
            trans = _strip_model_prefix(deep_clean_title(trans.strip() or pure_eng))
            # 再用代码拼回固定前缀
            results[i] = f"{_fmt_prefix(typ, num)}：{trans}"
            
        results[i] = results[i].rstrip('。') # 去掉句号

        # 实时保存 & 预览
        with open(trans_file, "w", encoding="utf-8") as f:
            f.write("\n".join(results) + "\n")
        preview = results[i] if len(results[i]) <= 60 else results[i][:57] + "..."
        logger.info(f"第 {i+1}/{total} 行完成 → {preview}")

    return results

# ==============================
# 8. 其余核心函数
# ==============================
# 缓存检查
def is_file_recent(path: str) -> bool:
    if not os.path.exists(path):
        return False
    return datetime.now() - datetime.fromtimestamp(os.path.getmtime(path)) <= timedelta(hours=HOURS_RECENT)

# 中间文件命名 + 过期自动清理（关键文件
def build_files_config(prefix: str) -> Dict[str, str]:
    PROCESSING_DIR.mkdir(exist_ok=True)
    
    files = {
        "toc_xml_file":     PROCESSING_DIR / f"{prefix}_bkm.xml",      # 原始书签 XML
        "content_file":     PROCESSING_DIR / f"{prefix}_content.txt",  # 纯英文标题
        "prompt_file":      PROCESSING_DIR / f"{prefix}_prompt.txt",   # 翻译指导书（最关键）
        "translation_file": PROCESSING_DIR / f"{prefix}_trans.txt",    # 中文翻译结果
        "trans_xml_file":   PROCESSING_DIR / f"{prefix}_trans.xml"     # 最终写回的 XML
    }
    
    # 强制：如果任意一个关键文件过期，就全部删掉重新生成
    # 这样保证翻译指导书、翻译结果、行数永远和最新 PDF 同步
    critical_files = [
        files["toc_xml_file"],
        files["content_file"],
        files["prompt_file"],
        files["translation_file"]
    ]
    
    any_expired = False
    for f in critical_files:
        if f.exists() and not is_file_recent(str(f)):
            any_expired = True
            break
    
    if any_expired:
        logger.info("检测到缓存已过期，强制清理旧中间文件...")
        for f in files.values():
            if f.exists():
                try:
                    f.unlink()
                    logger.info(f"   已删除：{f.name}")
                except:
                    pass
    
    return {k: str(v) for k, v in files.items()}

# 导出 TOC → XML
def export_toc_to_xml(pdf_path: str, config: Dict[str, str]) -> bool:
    try:
        doc = fitz.open(pdf_path)
        toc = doc.get_toc()
        doc.close()
        if not toc:
            logger.info("   无目录，跳过")
            return False

        skip = {"front cover", "title page", "copyright", "brief contents", "contents", "preface"}
        filtered = [item for item in toc if not any(s in item[1].strip().lower() for s in skip)]
        if not filtered:
            logger.info("   目录为空（仅封面），跳过")
            return False

        root = ET.Element("PDF_Bookmarks")
        root.set("source", os.path.basename(pdf_path))
        root.set("total_items", str(len(filtered)))
        stack = [root]

        for level, title, page in filtered:
            title = deep_clean_title(title)
            item = ET.Element("ITEM")
            item.set("NAME", title)
            item.set("PAGE", str(page))
            item.set("LEVEL", str(level))
            while len(stack) > level:
                stack.pop()
            stack[-1].append(item)
            stack.append(item)

        pretty = minidom.parseString(ET.tostring(root, 'utf-8')).toprettyxml(indent="  ")
        with open(config["toc_xml_file"], "w", encoding="utf-8") as f:
            f.write(pretty)
        # logger.debug(f"   导出 TOC → {len(filtered)} 项")
        return True
    except Exception as e:
        logger.debug(f"   导出失败: {e}")
        return False

# 替换书签名称
def replace_bookmark_names_by_order(config: Dict[str, str]) -> bool:
    try:
        with open(config["content_file"], 'r', encoding='utf-8') as f:
            orig = [l.strip() for l in f if l.strip()]
        with open(config["translation_file"], 'r', encoding='utf-8') as f:
            trans = [l.strip() for l in f if l.strip()]
        if len(orig) != len(trans):
            logger.debug(f"   行数不匹配！原文 {len(orig)} vs 译文 {len(trans)}")
            return False

        trans_map = dict(zip(orig, trans))
        tree = ET.parse(config["toc_xml_file"])
        root = tree.getroot()

        def replace(e):
            if e.tag == "ITEM":
                name = deep_clean_title(e.get("NAME", "")).strip()
                if name in trans_map:
                    e.set("NAME", trans_map[name])
            for child in e:
                replace(child)

        replace(root)
        tree.write(config["trans_xml_file"], encoding='utf-8', xml_declaration=True)
        logger.info(f"书签中文替换完成 → {config['trans_xml_file']}")
        return True
    except Exception as e:
        logger.error(f"书签中文替换失败: {e}")
        return False

# 写回 PDF
def pdf_import_toc_xml(toc_trans_xml, tgt_pdf: str) -> bool:
    if not os.path.exists(toc_trans_xml):
        return False
    try:
        tree = ET.parse(toc_trans_xml)
        new_toc = []
        def extract(e, lvl=1):
            for item in e.findall("ITEM"):
                name = item.get("NAME")
                page = int(item.get("PAGE"))
                level = int(item.get("LEVEL", lvl))
                new_toc.append([level, name, page])
                extract(item, level + 1)
        extract(tree.getroot())

        if BACKUP_TARGET:
            backup_path = tgt_pdf + ".backup"
            if os.path.exists(tgt_pdf):
                shutil.copy2(tgt_pdf, backup_path)
                logger.debug(f"   备份 → {os.path.basename(backup_path)}")

        doc = fitz.open(tgt_pdf)
        doc.set_toc(new_toc)
        doc.saveIncr()
        doc.close()
        logger.info(f"写回成功 → {os.path.basename(tgt_pdf)}")

    except Exception as e:
        logger.warn(f"写回失败: {e}")

    return True

import xml.etree.ElementTree as ET
def xml_to_tree(xml_str: str) -> str:
    """返回纯文本树形书签，缩进 4×(LEVEL-1) 空格"""
    root = ET.fromstring(xml_str)
    lines = []
    for item in root.iter('ITEM'):
        level = int(item.get('LEVEL', 1))
        indent = ' ' * 4 * (level - 1)
        name = item.get('NAME', '')
        lines.append(f'{indent}{name}')
    return '\n'.join(lines)


# ==============================
# 主流程 —— 全部逐行翻译版
# ==============================
def translate_toc(src_path: str, prefix: str) -> str:
    logger.debug("-"*60)
    logger.debug("PDF 目录本地 Ollama 翻译系统启动")
    logger.debug(f"模型：{OLLAMA_MODEL} @ {OLLAMA_BASE_URL}")
    logger.debug("-"*60)
    
    basename = os.path.basename(src_path)
    if not prefix:
        logger.debug(f"   无前缀，跳过 {basename}")
        return ""
    cfg = build_files_config(prefix)   # ← 这里会自动判断是否需要清理缓存

    logger.info(f"翻译书籍目录：{basename}")
    logger.info(f"使用前缀：{prefix}")

    # 1. 导出 TOC 到 XML
    if not export_toc_to_xml(src_path, cfg):
        return ""

    # 2. 提取所有标题文本到 content.txt
    if not is_file_recent(cfg["content_file"]):
        titles = []
        tree = ET.parse(cfg["toc_xml_file"])
        for item in tree.getroot().findall(".//ITEM"):
            t = deep_clean_title(item.get("NAME", ""))
            if t.strip():
                titles.append(t)
        with open(cfg["content_file"], "w", encoding="utf-8") as f:
            f.write("\n".join(titles) + "\n")
        logger.info(f"提取目录文本 → {len(titles)} 行")
    else:
        with open(cfg["content_file"], "r", encoding="utf-8") as f:
            title_count = len([l for l in f if l.strip()])
        logger.info(f"使用缓存的目录文本 → {title_count} 行")

    # 3. 生成专业的翻译指导书（只生成一次，永久缓存）
    guide_file = cfg["prompt_file"]  # 复用 prompt_file 存翻译指导
    translation_guide = generate_translation_guide(cfg["content_file"], guide_file)

    # 4. 全部逐行翻译
    with open(cfg["content_file"], "r", encoding="utf-8") as f:
        all_english_lines = [line.strip() for line in f if line.strip()]

    logger.info(f"开始逐行翻译，共 {len(all_english_lines)} 行")
    chinese_lines = translate_line_by_line(all_english_lines, translation_guide, cfg["translation_file"])

    # 5. 最终行数检查
    if len(chinese_lines) != len(all_english_lines):
        logger.error(f"严重错误：翻译行数不匹配！{len(chinese_lines)} vs {len(all_english_lines)}")
        return ""

    with open(cfg["translation_file"], "w", encoding="utf-8") as f:
        f.write("\n".join(chinese_lines) + "\n")
    logger.info(f"翻译完成！生成 {cfg['translation_file']}")

    # 6. 替换 XML 中的书签名称
    if not replace_bookmark_names_by_order(cfg):
        logger.error("书签替换失败")
        return ""

    # 7. 保存翻译后的 xml 到指定位置
    trans_xml_file = PROCESSING_DIR / f"{prefix}_trans.xml"
    if Path.exists(trans_xml_file):
        shutil.copy(trans_xml_file, TOC_DIR)
    else:
        logger.error(f"ErrOR：翻译后的 {trans_xml_file} 未找到")
        
    logger.info(f"目录翻译完成 → {basename}")
    logger.debug("   过程文件生成：")
    for k, v in cfg.items():
        exists = "Yes" if os.path.exists(v) else "No"
        logger.debug(f"     {k:15} → {Path(v).name} [{exists}]")
        
    return os.path.join(TOC_DIR, f"{prefix}_trans.xml")
        
    
def batch_translate_toc_and_write_tgt(WORK_DIR: str):
    # 批量模式
    if not WORK_DIR or not os.path.isdir(WORK_DIR):
        logger.debug("批量模式未配置")
        return
    work = Path(WORK_DIR)
    sources = [str(p) for p in work.glob("*.pdf") if not any(p.name.endswith(s) for s in TARGET_SUFFIXES)]

    for src in sources:
        # from pathlib import Path
        # path = Path("/home/user/data/file.txt")
        # logger.debug(f"完整路径: {path}")            # /home/user/data/file.txt
        # logger.debug(f"文件名（带扩展名）: {path.name}")   # file.txt
        # logger.debug(f"文件名（不带扩展名）: {path.stem}")  # file
        # logger.debug(f"扩展名: {path.suffix}")        # .txt
        # logger.debug(f"父目录: {path.parent}")        # /home/user/data
        # logger.debug(f"根目录: {path.anchor}")        # / (Linux) 或 C:\ (Windows)
        base = Path(src).stem
        # 使用相似性比较而不是精确匹配
        targets = []
        src_filename = Path(src).name
        logger.debug(f"正在处理源文件: {src_filename}")
        
        for p in work.iterdir():
            if p.suffix == ".pdf" and is_target_file(p.name):
                # 移除后缀得到基本文件名进行比较
                target_filename = p.name
                matched_suffix = is_target_file_2(p.name)
                if matched_suffix:
                    # 移除匹配的后缀（包括.pdf）
                    tgt_base = target_filename[:-len(matched_suffix)]
                
                src_base = src_filename[:-4]
                # 如果文件名高度相似，则认为是匹配的 
                if is_highly_similar(src_base, tgt_base, threshold=80): # 如果超过80%相似度，则可以进行记录
                    if is_highly_similar(src_base, tgt_base, threshold = 95):
                        targets.append(str(p))
                    else:
                        similarity = SequenceMatcher(None, src_base, tgt_base).ratio()
                        logger.debug(f"  比较 '{src_base}' 和 '{tgt_base}': 相似度={similarity:.4f}")

        if targets:
            pdf_import_toc_xml(src, targets)

# ==============================
# 入口
# ==============================
def main():
    translate_toc()


if __name__ == "__main__":
    main()