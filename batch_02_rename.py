# pdf_rename.py
import os
from typing import List

# 导入cip_parser模块中的函数
from batch_01_cip_parser import cip_parser
# 导入_utils模块中的函数
from _utils import logger, TARGET_SUFFIXES,is_highly_similar

# 脚本所在路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 重命名pdf文件
def rename_related_pdf(src_file: str, new_filename: str) -> str:
    
    file_dir = os.path.dirname(src_file)
    new_file_path = os.path.join(file_dir, new_filename)

    # 检查目标文件是否已存在
    if os.path.exists(new_file_path):
        # logger.debug(f"相关目标文件已存在，跳过: {new_file_path}")
        return
        
    logger.info(f"重命名相关文件: {src_file} -> {new_filename}")
    os.rename(src_file, new_file_path)
    
    return os.path.isfile(new_file_path)

    
# 从翻译文件目录寻找相关 pdf
def rename_related_pdfs(directory: str, src_filename: str, new_filename: str) -> int:
    """重命名与主文件相关的目标文件
    
    Args:
        root_directory: 根目录路径
        src_filename: 原始文件名
        new_filename: 新文件名
        
    Returns:
        bool: 是否成功重命名了相关文件
    """
    renamed_count = 0
    related_files = find_related_files(directory, base_name= os.path.splitext(src_filename)[0])

    # 重命名所有相关文件
    for related_file_path in related_files:
        # 提取相对路径信息以保持目录结构
        # related_dir = os.path.dirname(related_file_path)
        related_filename = os.path.basename(related_file_path)
        logger.info(f"重命名相关文件: {related_filename}")
        
        # 构造新的相关文件名
        for suffix in TARGET_SUFFIXES:
            if related_filename.endswith(suffix):
                # 使用新主文件名加上原有后缀构成新文件名
                new_related_filename = f"{new_filename[:-4]}{suffix}"
                success = rename_related_pdf(related_file_path, new_related_filename)
                if success:
                    renamed_count += 1
                break
    
    return renamed_count


def find_related_files(directory: str, base_name: str) -> List[str]:
    """查找与基础文件名相关的文件（带特定后缀的文件）
    
    Args:
        directory: 搜索目录
        base_name: 基础文件名（不含扩展名）
        
    Returns:
        List[str]: 相关文件路径列表
    """
    logger.info(f"正在搜索目录: {directory}")
    logger.info(f"正在搜索相关文件: {base_name}")
    related_files = []
    for suffix in TARGET_SUFFIXES:
        # 构造带后缀的文件名模式
        pattern = f"{base_name}{suffix}"
        for root, _, files in os.walk(directory):
            for file in files:
                if is_highly_similar(file, pattern):
                    related_files.append(os.path.join(root, file))
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