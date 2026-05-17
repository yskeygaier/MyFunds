#!/bin/bash
export DOUBAO_API_KEY="ark-187eaceb-af7b-4d15-b405-eabb6d58e041-602bd"
export DOUBAO_MODEL="doubao-seed-1-6-vision-250815"
export DOUBAO_ENDPOINT="https://ark.cn-beijing.volces.com/api/v3/chat/completions"

cd "$(dirname "$0")"
nohup venv2/bin/python app.py > /tmp/app.log 2>&1 &
echo "App started PID=$!"
