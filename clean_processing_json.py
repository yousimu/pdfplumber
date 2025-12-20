#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
清理 processing.json 中无效条目
删除 books_id 为空或者 safe_title 为空的条目
"""

import json
from pathlib import Path
from batch import PROCESSING_JSON_PATH

def clean_processing_json():
    """
    清理 processing.json 文件中 books_id 为空或者 safe_title 为空的条目
    """
    
    # 检查文件是否存在
    if not PROCESSING_JSON_PATH.exists():
        print(f"错误: 找不到文件 {PROCESSING_JSON_PATH}")
        return
    
    # 读取 processing.json 文件
    try:
        with open(PROCESSING_JSON_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"错误: JSON 文件格式不正确: {e}")
        return
    except Exception as e:
        print(f"错误: 读取文件失败: {e}")
        return
    
    # 统计原始条目数量
    original_count = len(data)
    
    # 检查重复的 books_id 和 isbn
    books_id_map = {}
    isbn_map = {}
    
    for i, entry in enumerate(data):
        books_id = entry.get("books_id", "")
        isbn = entry.get("isbn", "")
        
        if books_id:
            if books_id not in books_id_map:
                books_id_map[books_id] = []
            books_id_map[books_id].append((i, entry))
            
        if isbn:
            if isbn not in isbn_map:
                isbn_map[isbn] = []
            isbn_map[isbn].append((i, entry))
    
    # 显示重复的 books_id
    duplicate_books_id_found = False
    for books_id, entries in books_id_map.items():
        if len(entries) > 1:
            if not duplicate_books_id_found:
                print("\n发现重复的 books_id:")
                duplicate_books_id_found = True
                
            print(f"\nbooks_id '{books_id}' 出现 {len(entries)} 次:")
            for index, entry in entries:
                standard_name = entry.get("standard_name", "Unknown")
                print(f"  - 条目 {index+1}: 文件 {standard_name}")
                
    # 显示重复的 isbn
    duplicate_isbn_found = False
    for isbn, entries in isbn_map.items():
        if len(entries) > 1:
            if not duplicate_isbn_found:
                print("\n发现重复的 isbn:")
                duplicate_isbn_found = True
                
            print(f"\nisbn '{isbn}' 出现 {len(entries)} 次:")
            for index, entry in entries:
                standard_name = entry.get("standard_name", "Unknown")
                print(f"  - 条目 {index+1}: 文件 {standard_name}")
    
    # 筛选出有效的条目（books_id 和 safe_title 都不为空）
    cleaned_data = []
    removed_entries = []
    
    for entry in data:
        books_id = entry.get("books_id", "")
        safe_title = entry.get("safe_title", "")
        
        # 如果 books_id 和 safe_title 都不为空，则保留该条目
        if books_id and safe_title:
            cleaned_data.append(entry)
        else:
            # 记录被移除的条目
            standard_name = entry.get("standard_name", "Unknown")
            removed_entries.append({
                "standard_name": standard_name,
                "books_id": books_id,
                "safe_title": safe_title
            })
    
    # 统计清理后的条目数量
    cleaned_count = len(cleaned_data)
    removed_count = original_count - cleaned_count
    
    # 显示被移除的条目
    if removed_entries:
        print("\n被移除的条目:")
        for entry in removed_entries:
            print(f"  - 文件: {entry['standard_name']}")
            print(f"    books_id: '{entry['books_id']}'")
            print(f"    safe_title: '{entry['safe_title']}'")
            print()
    
    # 保存清理后的数据
    try:
        backup_path = PROCESSING_JSON_PATH.with_suffix('.json.backup')
        # 创建备份
        with open(PROCESSING_JSON_PATH, 'r', encoding='utf-8') as original, \
             open(backup_path, 'w', encoding='utf-8') as backup:
            backup.write(original.read())
        
        # 写入清理后的数据
        with open(PROCESSING_JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(cleaned_data, f, ensure_ascii=False, indent=2)
        
        print("\n清理完成!")
        print(f"原始条目数量: {original_count}")
        print(f"清理后条目数量: {cleaned_count}")
        print(f"移除的条目数量: {removed_count}")
        print(f"原始文件已备份至: {backup_path}")
        
    except Exception as e:
        print(f"错误: 保存文件失败: {e}")
        return

if __name__ == "__main__":
    clean_processing_json()