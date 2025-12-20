import requests
from typing import Dict
import xml.etree.ElementTree as ET
from pathlib import Path
from _utils import logger, WIKI_BASE_PATH, get_safe_title, PROXIES,PROCESSING_DIR

MD_DIR = WIKI_BASE_PATH
COVERS_DIR = WIKI_BASE_PATH / "covers"


def download_and_save_cover_image(meta, img_filename:str) -> Path:
    """
    下载并保存图书封面图片
    """
    try:
        # 获取图片链接
        thumbnail_url = meta.get("imageLink")
        if not thumbnail_url:
            return None

        # 目标图片路径
        img_path = COVERS_DIR / img_filename

        # 如果图片已经存在，直接返回路径，取消重新下载
        if img_path.exists():
            return img_path

        # 下载图片需要挂代理
        img_resp = requests.get(thumbnail_url, timeout=30, proxies=PROXIES)
        img_resp.raise_for_status()

        # 保存图片
        with open(img_path, 'wb') as f:
            f.write(img_resp.content)

        return img_path
    except Exception as e:
        logger.warn(f"下载封面失败: {e}")
        return None
    
# -------------- Markdown 输出 --------------
def build_markdown(meta) -> Path:
    """
    生成 Markdown 文件
    """

    safetitle = get_safe_title(meta)
    md_file = MD_DIR / f"{safetitle}.md"
    
    img_file = COVERS_DIR
    # 处理封面图片，避免上次下载失败，总是尝试
    if "imageLink" in meta and meta["imageLink"]:
        img_file = download_and_save_cover_image(meta, f"{meta["id"]}.jpg")
        logger.info(f"封面图片已保存: {img_file}")
    
    # 检查 MD 文件是否已经存在
    if md_file.exists():
        logger.warn(f"Markdown 文件已存在:[ {md_file} ]")
        return md_file
    
    logger.info(f"正在生成 Markdown 文件:[ {md_file.name} ]")
    
    en_title = meta["title"]
    # 使用列表来构建 Markdown 内容
    md_content = []
    
    md_content.append(f"## 书籍信息")
    md_content.append("")
    if meta.get("subtitle"):
        md_content.append(f"- **书名**：{en_title}: {meta.get('subtitle')}")
    else:
        md_content.append(f"- **书名**：{en_title}")
    md_content.append(f"- **中文译名**：{meta['title_zh']}")
    
    # 添加作者信息
    authors = meta.get("authors")
    if authors:
        if isinstance(authors, list):
            authors_str = ", ".join(authors)
        else:
            authors_str = str(authors)
        md_content.append(f"- **作者**：{authors_str}")
    else:
        md_content.append(f"- **作者**：暂无数据")
    
    if meta.get("edition") and int(meta.get("edition")) > 1:
        md_content.append(f"- **版本**：{meta.get('edition')}")
    
    md_content.append("")
    
    # 检查图片文件是否存在并且有效
    if img_file and img_file.is_file():
        md_content.append(f"![封面](covers/{img_file.name})")
        md_content.append("")
    else: # 如果没有封面图片，则使用占位符；包括图片下载失败的情况
        md_content.append(f"![封面](covers/placeholder.png ':size=30%')")
        md_content.append("")
    
    # 添加其他信息
    md_content.append(f"- **出版社**：{meta.get("publisher") or "无"}")
    md_content.append(f"- **出版日期**：{meta.get("publishedDate") or "无"}")
    
    # 分类信息
    categories = meta.get("categories")
    if categories:
        if isinstance(categories, list):
            categories_str = ", ".join(categories)
        else:
            categories_str = str(categories)
        md_content.append(f"- **图书分类**：{categories_str}")
    else:
        md_content.append(f"- **图书分类**：无")
    
    # ISBN 
    if meta.get("isbn"):
        md_content.append(f"- **ISBN**：{meta.get("isbn")}")
    else:
        md_content.append(f"- **ISBN**：无")
    
    md_content.append(f"- **页数**：{meta.get("pageCount") or "无"}")
    md_content.append(f"- **索引文件名**：{meta.get("filename")}")
    md_content.append("")
    
    # 简介
    description = meta.get("description_zh") or meta.get("description") or "暂无简介"
    md_content.append("### 简介")
    md_content.append("")
    md_content.append(description)
    md_content.append("")
    
    # 目录
    md_content.append("### 书目")
    md_content.append("```content")
    logger.debug(f"正在给 md 文件生目录树形结构")
    toc = get_toc_from_xml(meta)
    if not toc.strip():
        logger.warn("目录树形结构为空")
        md_content.append("目录待补充")
    else:
        logger.info(f"目录树形结构已生成")
        md_content.append(toc)
    md_content.append("```")
    md_content.append("")

    
    # 将列表内容写入文件
    with open(md_file, "w", encoding="utf-8") as f:
        f.write("\n".join(md_content))
    
    return md_file

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

def get_toc_from_xml(meta:Dict[str,str]) -> str:
    # 获取前缀
    filename = meta.get("filename")
    if not filename:
        logger.warn("元数据中缺少filename字段")
        return ""
    
    prefix = meta.get("id")[-12:]
    if not prefix:
        logger.warn(f"无法获取文件 {filename} 的前缀")
        return ""
    
    # 构造XML文件路径
    xml_file = PROCESSING_DIR / f"{prefix}_trans.xml"
    
    # 检查XML文件是否存在
    if not xml_file.exists():
        logger.warn(f"目录XML文件不存在: {xml_file}")
        return ""
    
    # 读取XML内容
    try:
        with open(xml_file, encoding='utf-8') as f:
            xml_content = f.read()
    except Exception as e:
        logger.warn(f"无法读取目录XML文件 {xml_file}: {e}")
        return ""
    
    # 解析XML并转换为文本树
    try:
        content_tree = xml_to_tree(xml_content)
        return content_tree
    except Exception as e:
        logger.warn(f"解析目录XML时出错: {e}")
        return ""
