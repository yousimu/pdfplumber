#!/bin/bash

# 一键安装脚本，用于初始化PDFPlumber项目环境

set -e  # 遇到错误时退出

echo "开始初始化PDFPlumber项目环境..."

# 创建虚拟环境
echo "创建Python虚拟环境..."
python3 -m venv venv
source venv/bin/activate

# 升级pip
echo "升级pip..."
pip install --upgrade pip

# 安装依赖
echo "安装项目依赖..."
pip install -r requirements.txt

# 拷贝环境配置文件
echo "拷贝环境配置文件..."
cp data/env.sample .env

echo ""
echo "安装完成！"
echo ""
echo "请执行以下操作："
echo "1. 激活虚拟环境: source venv/bin/activate"
echo "2. 修改配置文件: 编辑 .env 文件，根据实际环境填写相应配置"
echo "3. 查看配置说明: cat data/env.sample 参考配置项说明"
echo ""
echo "之后就可以运行项目了，例如:"
echo "python pdfplumber/batch.py"