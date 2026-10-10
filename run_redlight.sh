#!/bin/sh
# 红灯先验：当前未实现模块的测试
cd "$(dirname "$0")"
python3 -m unittest tests.test_protocol_adapter -v
