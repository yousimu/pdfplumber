#!/bin/bash

# 一键安装脚本，用于初始化PDFPlumber项目环境

set -e  # 遇到错误时退出

echo "开始初始化PDFPlumber项目环境..."

# 获取当前主机名
HOSTNAME=$(hostname)
CONFIG_FILE="pdfplumber/config_${HOSTNAME}.json"

echo "检测到主机名: $HOSTNAME"
echo "将创建配置文件: $CONFIG_FILE"

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

# 生成配置文件
echo "生成配置文件: $CONFIG_FILE"
cat > "$CONFIG_FILE" << EOF
{
    "default": {
        "WIKI_BASE_PATH": "/path/to/your/wiki/base/path",
        "EOOKS_PATH": "/path/to/your/ebooks/path",
        "LOG_LEVEL": "INFO",
        "OLLAMA_BASE_URL": "http://localhost:11434",
        "OLLAMA_MODEL": "qwen2.5:14b-instruct-q4_K_M",
        "DEEPSEEK_API_KEY": "your_deepseek_api_key_here",
        "PROXIES": {
            "http": "",
            "https": ""
        }
    },
    "$HOSTNAME": {
        "hostname": "$HOSTNAME",
        "WIKI_BASE_PATH": "/path/to/your/wiki/base/path",
        "EOOKS_PATH": "/path/to/your/ebooks/path",
        "LOG_LEVEL": "INFO",
        "OLLAMA_BASE_URL": "http://localhost:11434",
        "OLLAMA_MODEL": "qwen2.5:14b-instruct-q4_K_M",
        "DEEPSEEK_API_KEY": "your_deepseek_api_key_here",
        "PROXIES": {
            "http": "",
            "https": ""
        }
    }
}
EOF

# 显示配置文件示例说明
cat > CONFIGURATION_README.md << EOF
# 配置文件说明

## 配置项说明

- **WIKI_BASE_PATH**: wiki基础路径，用于存放处理后的文件
- **EOOKS_PATH**: 电子书原始文件路径
- **LOG_LEVEL**: 日志级别 (DEBUG/INFO/WARNING/ERROR)
- **OLLAMA_BASE_URL**: Ollama服务地址
- **OLLAMA_MODEL**: 使用的Ollama模型
- **DEEPSEEK_API_KEY**: DeepSeek API密钥（如果使用DeepSeek）
- **PROXIES**: 代理设置，如果不需要代理请保持为空字符串

## 配置文件使用说明

1. 根据你的环境修改$CONFIG_FILE中的配置项
2. WIKI_BASE_PATH和EOOKS_PATH需要是实际存在的目录
3. 如果使用Ollama，确保OLLAMA_BASE_URL指向正确的服务地址
4. 如果使用代理，在PROXIES中设置相应的http和https代理地址
5. 如果使用DeepSeek，将DEEPSEEK_API_KEY替换为实际的API密钥

## 多环境支持

配置文件支持多环境配置，会根据主机名自动选择对应的配置项。
如果找不到对应主机名的配置，则会使用default配置。
EOF

echo ""
echo "安装完成！"
echo ""
echo "请执行以下操作："
echo "1. 激活虚拟环境: source venv/bin/activate"
echo "2. 修改配置文件: $CONFIG_FILE"，完成后重命名为 config.json 后放入 pdfplumber/ 下
echo "3. 查看详细配置说明: cat README.md"
echo ""
echo "之后就可以运行项目了，例如:"
echo "python pdfplumber/batch.py"