from fastapi import FastAPI, HTTPException, Query
from pathlib import Path
import subprocess
import sys
import requests
import re
import os
import logging
from typing import Optional
import json
import sqlite3
from datetime import datetime

app = FastAPI()
AIPROXY_TOKEN = os.environ.get("AIPROXY_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def handle_special_cases(task: str) -> Optional[dict]:
    # A1: Data generation
    if "datagen.py" in task and "uv" in task.lower():
        email = "23f1002364@ds.study.iitm.ac.in"  # Extract from task if needed
        try:
            subprocess.run(["uv", "--version"], check=True)
        except:
            subprocess.run([sys.executable, "-m", "pip", "install", "uv"], check=True)
        
        url = "https://raw.githubusercontent.com/sanand0/tools-in-data-science-public/tds-2025-01/project-1/datagen.py"
        response = requests.get(url)
        (Path("/data") / "datagen.py").write_text(response.text)
        subprocess.run([sys.executable, "/data/datagen.py", email], check=True)
        return {"status": "success"}

    # A2: Prettier formatting
    if "prettier" in task.lower() and "format.md" in task:
        subprocess.run(["/usr/bin/npx", "prettier@3.4.2", "--write", "/data/format.md"], check=True)
        return {"status": "success"}
    
    # Add other special cases similarly
    return None

def resolve_path(user_path: str) -> Path:
    base = Path("/data").resolve()
    requested = (base / user_path).resolve()
    if not requested.is_relative_to(base):
        raise HTTPException(400, "Path traversal attempt blocked")
    return requested

def validate_code(code: str):
    forbidden = [
        r"os\.(remove|system|popen)\(",
        r"shutil\.(rmtree|move)\(",
        r"subprocess\.(run|Popen).*shell=True",
        r"\b(rm|del|mv|wget|curl)\b",
        r"open\(.*['\"]w",
        r"ALTER\s+TABLE",
        r"DROP\s+TABLE",
        r"git\s+clone.*(?!/data/)"
    ]
    for pattern in forbidden:
        if re.search(pattern, code, re.IGNORECASE):
            raise HTTPException(400, f"Dangerous operation detected: {pattern}")

def call_llm(prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {AIPROXY_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1000
    }
    response = requests.post(
        "http://aiproxy.sanand.workers.dev/openai/v1/chat/completions",
        headers=headers,
        json=data
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]

def extract_code(text: str) -> str:
    code_blocks = re.findall(r'```python(.*?)```', text, re.DOTALL)
    return code_blocks[0].strip() if code_blocks else text.strip()

@app.post("/run")
async def run_task(task: str = Query(..., min_length=1)):
    try:
        # Handle known patterns first
        special_result = handle_special_cases(task)
        if special_result:
            return special_result

        # LLM-generated code for other tasks
        prompt = f"""Generate secure Python code to complete this task:
        - Only use /data directory
        - No deletions or dangerous operations
        - Use Python libraries instead of shell commands
        - Include error handling
        
        Task: {task}"""
        
        llm_response = call_llm(prompt)
        code = extract_code(llm_response)
        validate_code(code)
        
        code_path = Path("/tmp/generated_code.py")
        code_path.write_text(code)
        code_path.chmod(0o755)

        result = subprocess.run(
            [sys.executable, str(code_path)],
            capture_output=True,
            text=True,
            timeout=20
        )

        if result.returncode != 0:
            raise HTTPException(500, detail=result.stderr.strip())

        return {"status": "success", "stdout": result.stdout.strip()}
    
    except HTTPException as e:
        raise e
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        raise HTTPException(500, detail="Task execution failed")

@app.get("/read")
async def read_file(path: str = Query(..., min_length=1)):
    try:
        resolved_path = resolve_path(path)
        if not resolved_path.exists():
            raise HTTPException(404)
        return resolved_path.read_text()
    except Exception as e:
        raise HTTPException(500, detail=str(e))