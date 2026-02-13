#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import subprocess
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
DATA_PATH = APP_DIR / "data.json"
INDEX_PATH = APP_DIR / "index.html"
SCRIPT_PATH = APP_DIR / "fetch_moltbook_news.py"


app = Flask(__name__)


def _read_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json_file(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/")
def index():
    # 返回同目录下的 index.html
    return send_from_directory(APP_DIR, "index.html")


@app.get("/api/config")
def get_config():
    data = _read_json_file(CONFIG_PATH, default={})
    return jsonify(data)


@app.post("/api/config")
def post_config():
    payload = request.get_json(silent=True) or {}
    target_url = payload.get("target_url")
    item_limit = payload.get("item_limit")

    if not isinstance(target_url, str) or not target_url.strip():
        return jsonify({"error": "target_url 不能为空"}), 400

    try:
        item_limit_int = int(item_limit)
    except Exception:
        return jsonify({"error": "item_limit 必须是整数"}), 400

    item_limit_int = max(1, min(item_limit_int, 200))

    new_cfg = {"target_url": target_url.strip(), "item_limit": item_limit_int}
    _write_json_file(CONFIG_PATH, new_cfg)
    return jsonify(new_cfg)


@app.post("/api/run")
def run_job():
    if not SCRIPT_PATH.exists():
        return jsonify({"error": "fetch_moltbook_news.py 不存在"}), 500

    # 同步运行脚本，运行完成后才返回成功
    try:
        proc = subprocess.run(
            ["python3", str(SCRIPT_PATH.name)],
            cwd=str(APP_DIR),
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        return (
            jsonify(
                {
                    "error": "运行失败",
                    "returncode": e.returncode,
                    "stdout": e.stdout,
                    "stderr": e.stderr,
                }
            ),
            500,
        )

    return jsonify({"ok": True, "stdout": proc.stdout, "stderr": proc.stderr})


@app.get("/api/data")
def get_data():
    data = _read_json_file(DATA_PATH, default={})
    return jsonify(data)


if __name__ == "__main__":
    # 极简本地开发启动
    # 访问 http://127.0.0.1:5000
    app.run(host="127.0.0.1", port=5000, debug=True)

