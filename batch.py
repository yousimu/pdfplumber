
import json
import secrets
import string
from pathlib import Path
from _utils import logger, check_model_exists,is_target_file, load_json_file, get_safe_title, has_bookmarks
from _utils import OLLAMA_BASE_URL, OLLAMA_MODEL, WIKI_BASE_PATH, LOG_DIR, DATA_DIR, EOOKS_PATH, RENAME_PDF_FILES
import time  # 添加时间模块用于统计
from batch_01_cip_parser import cip_parser
from batch_02_rename import rename_related_pdfs, find_related_files
from batch_03_toc import translate_toc, pdf_import_toc_xml, TOC_DIR
from batch_04_md import build_markdown

"""
将处理的数据存在下面的 processing.json 文件中，避免重复做：
[
  {
  	"status":{                  # 处理的数据可以手动改为 true 或者 false，便于自动处理的时候识别再处理（不满意）或者不处理（难处理，手动处理）
    	"rename_done":true,			    # 是否已经重命名过，完成或者不再处理（准备手动处理）都设置为true
        "handle_isbn":true,		        # 和 norm_isbn 联动，如果为 true 且 norm_isbn 不为空，则说明百分百准确
    	"parse_metadata":true,          # 生成这个是一切的前提，如果
    	"request_google_api":false,     # 请求过，且 books_id 不以 NLJR- 开头，说明有 Google Book 数据
    	"trans_toc":true,               # 一方面用来生成 md 文件，一方面目录用来写入相关的 pdf 中
    	"build_md":true                 # 最终是为了展示，有这个说明一切都已经处理完毕，可以跳过所有步骤
    },
  	"books_id": "NLJR-erIu7vr0lFB2",    # 谷歌的id，或者自定义的随机 id（以 NLJR-xxxxx 前缀），同时也是封面图片名
    "norm_isbn": "9789351342939",       # 有 ISBN 则为书本，具有唯一性
    "original_name": "original_file_name.pdf",  # 原始文件名，用于跟踪重命名历史
    "standard_name": "Computer Programming (2014) - 计算机编程.pdf",          # 用来识别和查找相关文件，对比
    "safe_title": "computer-programming-nljr-eriu7vr0lfb2",                 # 用来命名 json 文件和 md 文件
    "meta_json":"computer-programming-nljr-eriu7vr0lfb2.json",              # 存有更多书籍信息，而非处理信息
    "md_file":"computer-programming-nljr-eriu7vr0lfb2.md"                   # 这个文件展示部分书籍信息，而且可以编辑。注意：一定要避免重复生成以免覆盖手动编辑内容
    "toc_trans_xml":""                  # 目录翻译的 xml 文件
    "keyinfo":{
      "is_book":true,                  # 确定是否是书籍
      "has_toc":true                   # 确定是否有目录
    }
  }

 ]
 
"""

# ----------- 配置 -------------

# 立即执行检查
check_model_exists(OLLAMA_BASE_URL, OLLAMA_MODEL)

# Processing JSON 文件路径
PROCESSING_JSON_PATH = DATA_DIR / "processing.json"
temporarily_file = LOG_DIR / "temporarily_files.txt"
MD_DIR = WIKI_BASE_PATH

def find_processed_file_info(filename: str) -> dict:
    """
    在processing.json中查找匹配的standard_name，并返回相关信息
    
    Args:
        filename: 要匹配的文件名
        
    Returns:
        dict: 匹配的条目信息，如果没有匹配项则返回空字典
    """
    # 如果processing.json文件不存在，则返回空字典
    if not PROCESSING_JSON_PATH.exists():
        return {}
    
    try:
        processing_data = load_json_file(PROCESSING_JSON_PATH)
        # 遍历processing.json中的所有条目
        for entry in processing_data:
            # 检查standard_name是否匹配
            if entry.get("standard_name") == filename:
                return entry
        return {}
    except Exception as e:
        logger.debug(f"读取或解析processing.json时出错: {e}")
        return {}

def save_processing_info(processed_info: dict):
    """
    将处理信息保存到 processing.json 文件中
    
    Args:
        processed_info: 处理信息字典
    """
    processing_data = []
    
    # 如果processing.json文件存在，则读取现有数据
    if PROCESSING_JSON_PATH.exists():
        try:
            processing_data = load_json_file(PROCESSING_JSON_PATH)
        except Exception as e:
            logger.debug(f"读取 processing.json 时出错: {e}")
            processing_data = []
    
    # 获取关键字段
    norm_isbn = processed_info.get("norm_isbn", "")
    books_id = processed_info.get("books_id", "")
    standard_name = processed_info.get("standard_name", "")
    
    existing_isbn_index = None
    existing_books_id_index = None
    existing_name_index = None
    merged = False
    
    # 首先按standard_name查找匹配条目
    for i, entry in enumerate(processing_data):
        if entry.get("standard_name") == standard_name:
            existing_name_index = i
            break
    
    # 然后按ISBN或books_id查找匹配条目
    if norm_isbn:  # 如果ISBN不为空
        for i, entry in enumerate(processing_data):
            # 检查是否有相同的ISBN
            if entry.get("norm_isbn") == norm_isbn:
                existing_isbn_index = i
                # 记录日志
                logger.debug(f"发现重复的ISBN: {norm_isbn}，合并条目")
                
                # 智能合并条目，保留非空值
                for key, value in processed_info.items():
                    if key == "status":
                        # 合并状态信息，确保所有为True的状态都被保留
                        status_old = entry.get("status", {})
                        status_new = processed_info.get("status", {})
                        for status_key in status_new:
                            if status_new[status_key]:
                                status_old[status_key] = True
                    elif key == "standard_name":
                        # 保留原有的standard_name（如果已存在）
                        if not entry.get("standard_name"):
                            entry[key] = value
                    else:
                        # 对于其他字段，只有当新值非空且旧值为空时才更新
                        if value and not entry.get(key):
                            entry[key] = value
                        elif not value and not entry.get(key):
                            entry[key] = value
                        
                merged = True
                break
    elif books_id:  # 如果没有ISBN但有books_id（非书籍类文档）
        for i, entry in enumerate(processing_data):
            # 检查是否有相同的books_id
            if entry.get("books_id") == books_id:
                existing_books_id_index = i
                # 记录日志
                logger.debug(f"发现重复的books_id: {books_id}，合并条目")
                
                # 智能合并条目，保留非空值
                for key, value in processed_info.items():
                    if key == "status":
                        # 合并状态信息，确保所有为True的状态都被保留
                        status_old = entry.get("status", {})
                        status_new = processed_info.get("status", {})
                        for status_key in status_new:
                            if status_new[status_key]:
                                status_old[status_key] = True
                    elif key == "standard_name":
                        # 保留原有的standard_name（如果已存在）
                        if not entry.get("standard_name"):
                            entry[key] = value
                    else:
                        # 对于其他字段，只有当新值非空且旧值为空时才更新
                        if value and not entry.get(key):
                            entry[key] = value
                        elif not value and not entry.get(key):
                            entry[key] = value
                        
                merged = True
                break
    
    # 根据是否合并过来决定更新策略
    if merged and (existing_isbn_index is not None or existing_books_id_index is not None):
        # 已经合并过了，不需要额外操作
        pass
    elif existing_name_index is not None:
        # 更新已有的文件名条目，智能合并信息
        existing_entry = processing_data[existing_name_index]
        
        # 智能合并所有字段
        for key, value in processed_info.items():
            if key == "status":
                # 合并状态信息，确保所有为True的状态都被保留
                status_old = existing_entry.get("status", {})
                status_new = processed_info.get("status", {})
                for status_key in status_new:
                    if status_new[status_key]:
                        status_old[status_key] = True
            else:
                # 对于其他字段，只有当新值非空且旧值为空时才更新
                if value and not existing_entry.get(key):
                    existing_entry[key] = value
                elif not value and not existing_entry.get(key):
                    existing_entry[key] = value
                    
    elif existing_isbn_index is not None and not merged:
        # 有相同ISBN但未合并（可能是首次处理），则更新该条目
        processing_data[existing_isbn_index] = processed_info
    elif existing_books_id_index is not None and not merged:
        # 有相同books_id但未合并（可能是首次处理），则更新该条目
        processing_data[existing_books_id_index] = processed_info
    else:
        # 添加新条目
        processing_data.append(processed_info)
    
    # 写回文件
    try:
        with open(PROCESSING_JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(processing_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"写入 processing.json 时出错: {e}")

def check_entry_exists(file_path: Path, entry: str) -> bool:
    """
    检查文件中是否已存在要写入的条目
    
    Args:
        file_path: 要检查的文件路径
        entry: 要查找的条目字符串
        
    Returns:
        bool: 如果文件中存在该条目返回True，否则返回False
    """
    if not file_path.exists():
        return False
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for line in lines:
                # 去除行首尾空白字符进行比较
                if line.strip() == entry.strip():
                    return True
        return False
    except Exception as e:
        logger.error(f"检查条目是否存在时出错: {e}")
        return False

## 总的方法
def main():
    """遍历目录并重命名PDF文件
    
    处理逻辑：
    1. 遍历指定目录下的所有PDF文件
    2. 跳过目标文件（带特定后缀的文件）
    3. 对基础文件调用cip_parser生成元数据和新文件名
    4. 重命名基础文件
    5. 查找并重命名相关的目标文件（保持后缀一致）
    
    Args:
        root_directory: 要遍历的根目录路径
        renamed_list_path: 已重命名文件列表路径
    """
    
    # 如果没有通过参数传递base_dir，则尝试从配置文件加载
    base_dir = EOOKS_PATH
    logger.info(f"使用配置文件加载的目录为：{base_dir}")
    logger.info(f"生成内容存储至：{WIKI_BASE_PATH}")
    if not base_dir.exists():
        logger.error("未找到有效的目录路径，请检查配置文件")
        return
    
    processed_count = 0
    renamed_count = 0
    
     # 遍历目录查找PDF文件
    for src_file in base_dir.rglob("*.pdf"):
        # 检查是否应该排除此文件（即是否为目标文件）
        if is_target_file(src_file.name):
            logger.debug(f"不属于目标文件，跳过: {src_file}")
            continue
        
        if check_entry_exists(temporarily_file, src_file.name):
            continue
        
        logger.info("="*100)
        # 在所有处理之前，先根据文件名去和 processing.json 中的 standard_name 去匹配，如果有匹配值，表明之前处理过，可以获取所有状态值
        processed_info = find_processed_file_info(src_file.name)
        if not processed_info:
            # 如果在processing.json中没有找到该文件的记录，则创建一个新条目
            # 初始化所有必要字段，包括可能通过后续步骤填充的 norm_isbn 和 books_id
            processed_info = {
                "status": {
                    "rename_done": False,                   
                    "parse_metadata": False,
                    "request_google_api": False,
                    "trans_toc": False,
                    "build_md": False
                },
                "books_id": "",              # 来自 Google Books API 或生成的 NLJR-xxxxx 格式 ID
                "norm_isbn": "",             # 规范化的 ISBN，用于唯一标识书籍
                "original_name": src_file.name,  # 原始文件名，用于跟踪重命名历史
                "standard_name": src_file.name,   # 标准文件名，作为匹配键，标准格式为 {title} ({year}){edition_part} - {title_zh}.pdf
                "safe_title": "",            # 安全文件名（用于 JSON/MD 文件）
                "meta_json": "",             # 对应的元数据 JSON 文件名
                "md_file": "",               # 生成的 Markdown 展示文件名
                "toc_trans_xml":"",
                "keyinfo": {
                    "is_book": True,        # 默认认为是书籍，有ISBN
                    "has_toc": True,        # 默认认为是有书签的
                }
            }
            # save_processing_info(processed_info) !!注：这里不能保存，否则会多出很多空 books_id 的条目
        else:
            # 如果找到了已有的记录，确保它有 original_name 字段
            if "original_name" not in processed_info:
                # 对于旧记录，我们无法知道确切的原始名称，所以使用 standard_name 作为后备
                processed_info["original_name"] = processed_info.get("standard_name", src_file.name)
                # save_processing_info(processed_info) !!注：这里不能保存，否则会多出很多空 books_id 的条目

        # 后面添加一个不是书籍的列表，供解析，避免调用大模型、api去解析浪费时间
        if processed_info.get("keyinfo", {}).get("is_book", True):
            # 添加书籍列表
            # BOOK_LIST.append(src_file)
            pass
        else:
            # 添加非书籍列表
            # NON_BOOK_LIST.load(src_file)
            continue

        # 获取状态信息
        status = processed_info.get("status", {})
        
        meta = {}
        
        # 初始化总计数器和时间统计
        total_start_time = time.time()
        phase1_time = 0  # 解析PDF元数据阶段时间
        phase2_time = 0  # 重命名文件阶段时间
        phase3_time = 0  # 翻译目录阶段时间
        phase4_time = 0  # 生成MD文件阶段时间
        
        ## ----------- 1、先解析 pdf，生成 meta.json -----------
        phase1_start = time.time()  # 阶段1开始时间
        # 调用 cip_parser 中的方法，获取新文件名。只管获取，如果失败则会记录在跳过列表中
        logger.info(f"----------- 1、先解析 pdf，生成 meta.json -----------")
        meta_json_path = WIKI_BASE_PATH / "meta"
        # 检查是否已经解析过元数据
        if status.get("parse_metadata", False):
            # 直接使用已有的meta_json文件
            meta_json_path = WIKI_BASE_PATH / "meta" / processed_info.get("meta_json")
            logger.info(f"使用已存在的元数据文件: {meta_json_path.name}")
            meta = load_json_file(meta_json_path)
            processed_info["books_id"] = meta.get('id', '')
            processed_info["norm_isbn"] = meta.get("isbn", False)
            processed_info["standard_name"] = meta.get("filename") # 每次都用元数据文件中的文件名；如果修改了pdf文件名，同步修改meta文件中的文件名
            processed_info["safe_title"] = get_safe_title(meta)
            save_processing_info(processed_info)
        else:
            # 需要重新解析PDF文件
            logger.info(f"处理基础文件: {src_file.name}")
            processed_info = cip_parser(src_file, processed_info)
            if not processed_info:
                cannot_be_processed_temporarily(src_file.name, "没有版权信息")
                continue
            if not processed_info.get("safe_title"):
                # 如果解析完成了，但是没有找到标题，则跳过。原因是 json 文件名来自 safe_title，而 safe_title 来自于标题名
                # 将没有标题的文件名写入单独的文件中备查
                cannot_be_processed_temporarily(src_file.name, "没有标题")
                continue
            meta_json_path = WIKI_BASE_PATH / "meta" / processed_info.get("meta_json")
            # save_processing_info(processed_info) !! 在没有获取标准名之前保存，都会产生两条记录，一条原文件名肯定没有books_id

            if meta_json_path.is_file():  # 如果还是目录，说明还没解析赋值
                meta = load_json_file(meta_json_path)
                # 更新ISBN和books_id信息
                norm_isbn = meta.get('isbn', '')
                books_id = meta.get('id', '') or meta.get('books_id', '')
                
                # 如果不是书，肯定没有 ISBN，则跳过。大概率要通过手动来设置
                if processed_info.get("keyinfo", {}).get("is_book", True):
                    if norm_isbn:
                        processed_info["norm_isbn"] = norm_isbn
                        processed_info["status"]["handle_isbn"] = True
                    else:
                        # 没有ISBN的情况下，设置handle_isbn为True，并确保books_id存在
                        processed_info["status"]["handle_isbn"] = True
                    
                if books_id:
                    processed_info["books_id"] = books_id
                else:
                    # 如果既没有ISBN也没有books_id，则生成一个
                    alphabet = string.ascii_letters + string.digits
                    books_id = "NLJR-"+''.join(secrets.choice(alphabet) for _ in range(12))
                    processed_info["books_id"] = books_id
                
                # 更新状态：元数据解析完成
                processed_info["status"]["parse_metadata"] = True
                processed_info["standard_name"] = meta.get("filename")
                processed_info["meta_json"] = meta_json_path.name
                processed_info["safe_title"] = get_safe_title(meta)
                # 保存更新后的状态
                save_processing_info(processed_info)
            else:
                logger.debug(f"元数据解析失败: {src_file}")
                # 可能需要在这里处理失败情况，比如跳过当前文件
                continue
        
        phase1_end = time.time()  # 阶段1结束时间
        phase1_time += (phase1_end - phase1_start)  # 累加阶段1时间
        
        ## ----------- 2、再根据 meta.json 重命名 pdf 文件及相关文件 -----------
        phase2_start = time.time()  # 阶段2开始时间
        logger.info(f"----------- 2、再根据 meta.json 重命名 pdf 文件及相关文件 -----------")
        new_file = Path(src_file.parent / meta.get('filename'))
        
                # 检查是否启用了重命名功能
        if not RENAME_PDF_FILES:
            logger.debug(f"重命名功能已禁用，跳过重命名文件 {src_file.name}")
        else:
            # 检查是否已经完成重命名
            if status.get("rename_done", False):
                if processed_info.get("standard_name") :
                    logger.debug(f"文件 {src_file} 已经完成重命名，跳过重命名步骤")
            elif status.get("rename_done", False) == False and new_file != src_file:
                try:
                    src_file.rename(new_file)
                except OSError as e:
                    if e.errno == 36:  # File name too long
                        cannot_be_processed_temporarily(src_file.name, "文件名过长")
                        continue
                if new_file.is_file():
                    renamed_count += 1
                    processed_count += 1
                    # 更新状态：重命名完成
                    processed_info["status"]["rename_done"] = True
                    # 保存更新后的状态
                    save_processing_info(processed_info)
            else:
                logger.debug(f"重命名文件 {src_file} 发生未知情况，请查明原因:{processed_info}")

        # 处理相关文件（带后缀的文件）
        related_renamed_count = rename_related_pdfs(src_file.parent, src_file, processed_info["standard_name"])
        renamed_count += related_renamed_count
        # repo_related_renamed_count=rename_related_pdfs("/Volumes/personal_folder/Resources/Library/EBooks/翻译库/对比翻译", src_file, new_filename)
        # renamed_count += repo_related_renamed_count
        phase2_end = time.time()  # 阶段2结束时间
        phase2_time += (phase2_end - phase2_start)  # 累加阶段2时间
        
        ## ----------- 3、如果 pdf 有书目，获取书目，并翻译成中文 -----------
        phase3_start = time.time()  # 阶段3开始时间
        logger.info(f"----------- 3、如果 pdf 有书目，获取书目，并翻译成中文 -----------")
        prefix = processed_info.get('books_id')[-12:]
        toc_trans_xml = TOC_DIR / f"{prefix}_trans.xml"
        has_toc = True
        if processed_info.get("trans_toc","") == False: # 如果没有处理过目录，才需要判断是否有书签，否则总是会卡1～2秒
            if new_file.is_file():
                has_toc = has_bookmarks(new_file)
            else:
                has_toc = False
                logger.info(f"文件 {new_file} 不存在目录，跳过翻译步骤")
        
        # 检查是否已经完成目录翻译
        if has_toc:
            check_tocxml = False
            if status.get("trans_toc", False) and toc_trans_xml.is_file():
                logger.info(f"文件 {src_file} 已经完成目录翻译，并且书目xml文件已存在，跳过翻译步骤")
                processed_info["toc_trans_xml"] = toc_trans_xml.name
                save_processing_info(processed_info)
                check_tocxml = True
            
            if check_tocxml == False and new_file.is_file():
                toc_trans_xml = translate_toc(new_file, prefix) 
                # 更新状态：目录翻译完成
                if toc_trans_xml.is_file():  # 确保翻译成功
                    processed_info["status"]["trans_toc"] = True
                    processed_info["toc_trans_xml"] = toc_trans_xml.name 
                    save_processing_info(processed_info)
                    check_tocxml = True
                else:
                    logger.debug(f"目录翻译文件不存在，翻译失败: {toc_trans_xml}")

            # 如果有相关翻译完成的文件，写入新的中文目录
            if check_tocxml and toc_trans_xml.is_file():
                relate_files = find_related_files(src_file.parent, src_file.stem)
                for related_pdf in relate_files:
                    if is_target_file(related_pdf.name):
                        pdf_import_toc_xml(toc_trans_xml,related_pdf)
                        continue
        else:
            processed_info["keyinfo"]["has_toc"] = False
            
        phase3_end = time.time()  # 阶段3结束时间
        phase3_time += (phase3_end - phase3_start)  # 累加阶段3时间
        
        ## ----------- 4、制作 md 文件，方便发布查看和编辑 -----------
        phase4_start = time.time()  # 阶段4开始时间
        logger.info(f"----------- 4、制作 md 文件，方便发布查看和编辑文 -----------")
        # 检查是否已经生成MD文件
        check_md = False
        if status.get("build_md", False):
            md_file = MD_DIR / f"{processed_info.get('safe_title')}.md"
            if md_file.is_file(): # 无论如何都能覆盖md文件，因为可能编辑过，除非没用自己删掉
                logger.info(f"文件 {src_file} 已经生成 Markdown 文件 {md_file.name}，跳过生成步骤")
                processed_info["status"]["build_md"] = True
                processed_info["md_file"] = md_file.name
                save_processing_info(processed_info)
                check_md = True
            else:
                logger.warn(f"processing.json 记录中的文件 {md_file.name} 不存在")
        
        if check_md == False:
            md_file = build_markdown(meta)
            if md_file.is_file():
                # 更新状态：MD文件生成完成
                processed_info["status"]["build_md"] = True
                processed_info["md_file"] = md_file.name
                save_processing_info(processed_info)
                logger.info(f"[info] 全流程跑完，文件 {meta.get('filename')} 生成 Markdown 文件成功 [ {processed_info.get('safe_title')}.md ]")
        phase4_end = time.time()  # 阶段4结束时间
        phase4_time += (phase4_end - phase4_start)  # 累加阶段4时间
        
        if check_md == False:
            # 计算总耗时
            total_end_time = time.time()
            total_time = total_end_time - total_start_time
            # 输出各阶段耗时统计
            logger.info(f"BOOK_ID:[ {processed_info.get("books_id")} ] 处理完成统计：\n  阶段1(解析PDF元数据)耗时 {phase1_time:.2f} 秒，\n  阶段2(重命名文件)耗时 {phase2_time:.2f} 秒，\n  阶段3(翻译目录)耗时 {phase3_time:.2f} 秒，\n  阶段4(生成MD文件)耗时 {phase4_time:.2f} 秒，\n  总耗时 {total_time:.2f} 秒")
                
    logger.debug(f"处理完成。总共处理了 {processed_count} 个文件，重命名了 {renamed_count} 个文件。")
    
def cannot_be_processed_temporarily(entry: str, reason: str = ""):
    """
    将无法处理的文件记录到暂时无法处理的记录文件中
    
    Args:
        temporarily_file: 暂时无法处理的记录文件路径
        entry: 要记录的文件名
        reason: 无法处理的原因（用于日志记录）
    """
    # 检查文件是否已存在于暂时无法处理的记录文件中
    if not check_entry_exists(temporarily_file, entry):
        logger.error(f"跳过文件 ({reason}): {entry}")
        with open(temporarily_file, "a", encoding="utf-8") as f:
            f.write(f"{entry}\n")

if __name__ == "__main__":
    main()