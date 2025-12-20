# pdf_rename.py
from typing import List
from pathlib import Path
# 导入_utils模块中的函数
from _utils import logger, TARGET_SUFFIXES, is_highly_similar

# 重命名pdf文件
def rename_related_pdf(src_file: Path, new_filename: str) -> bool:
    new_file = src_file.parent / new_filename

    # 检查目标文件是否已存在
    if new_file.exists():
        logger.debug(f"相关目标文件已存在，跳过: {new_file}")
        return
        
    logger.info(f"重命名相关文件: {src_file} -> {new_filename}")
    src_file.rename(new_file)
    
    return new_file.is_file()
    
# 从翻译文件目录寻找相关 pdf 并重命名
def rename_related_pdfs(Directory: Path, src_file: Path, new_filename: str) -> int:
    """重命名与主文件相关的目标文件
    
    Args:
        Directory (Path): 根目录路径，可能不再源文件目录找，而是去翻译库找
        src_file (Path): 带TARGET_SUFFIXES的原始文件，如"/x/x/xxx_dual.pdf"
        new_filename: 新文件名，带后缀，如"xxx.pdf"
        
    Returns:
        bool: 是否成功重命名了相关文件
    """
    renamed_count = 0
    related_files = find_related_files(Directory, src_file.stem) or []

    # 重命名所有相关文件
    for related_file in related_files:
        # 提取相对路径信息以保持目录结构
        logger.info(f"重命名相关文件: {related_file.name}")
        
        # 构造新的相关文件名
        for suffix in TARGET_SUFFIXES:
            if related_file.name.endswith(suffix):
                # 使用新主文件名加上原有后缀构成新文件名
                new_related_filename = f"{new_filename[:-4]}{suffix}"
                success = rename_related_pdf(related_file, new_related_filename) or False
                if success:
                    renamed_count += 1
                break
    
    return renamed_count


def find_related_files(Directory: Path, base_name: str) -> List[Path]:
    """查找与基础文件名相关的文件（带特定后缀的文件）
    
    Args:
        Directory: 搜索目录
        base_name: 基础文件名（不含扩展名）
        
    Returns:
        List[Path]: 相关文件路径列表
    """
    logger.info(f"正在搜索目录: {Directory}")
    logger.debug(f"正在搜索相关文件: {base_name}")
    
    related_files = []
    for suffix in TARGET_SUFFIXES:
        # 构造带后缀的文件名模式
        pattern = f"{base_name}{suffix}"
        for file in Directory.rglob(pattern):
            if is_highly_similar(file.name, pattern):
                related_files.append(file)
    logger.info(f"找到 {len(related_files)} 个相关文件")
    return related_files

# -------------------- 自测 --------------------
if __name__ == "__main__":
    file_basename = ""  # 替换为实际路径
    search_dir = ""  # 替换为实际路径
    find_related_files(search_dir,file_basename)
    
     # 测试rename_related_pdfs方法
    base_file = ""
    new_basename = ""
    renamed_count = rename_related_pdfs(search_dir, base_file, new_basename) # 从翻译目录中寻找相关文件并重命名
    logger.debug(f"成功重命名了 {renamed_count} 个相关文件")