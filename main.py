from fastapi import FastAPI, HTTPException, Query
import subprocess
from pathlib import Path
import os
import requests
import re
import logging

app = FastAPI()
AIPROXY_TOKEN = os.environ.get("AIPROXY_TOKEN")

logging.basicConfig(level=logging.INFO)

def resolve_path(user_path: str) -> Path:
    base = Path("/data").resolve()
    requested = (base / user_path).resolve()
    if not requested.is_relative_to(base):
        raise HTTPException(status_code=400, detail="Path outside /data is not allowed.")
    return requested

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
    try:
        response = requests.post("https://api.aiproxy.io/v1/chat/completions", headers=headers, json=data)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logging.error(f"Error calling LLM: {str(e)}")
        raise HTTPException(status_code=500, detail="LLM service error.")

def validate_code(code: str) -> None:
    forbidden_patterns = [
        r"os\.remove\(",
        r"shutil\.rmtree\(",
        r"subprocess\.run\(.*['\"]rm",
        r"subprocess\.run\(.*['\"]del",
        r"[\s\(]rm\s+",
        r"[\s\(]del\s+"
    ]
    for pattern in forbidden_patterns:
        if re.search(pattern, code):
            raise HTTPException(status_code=400, detail="Task involves deletion which is not allowed.")

def extract_code(text: str) -> str:
    code_blocks = re.findall(r'```python(.*?)```', text, re.DOTALL)
    if code_blocks:
        return code_blocks[0].strip()
    return text.strip()

@app.post("/run")
async def run_task(task: str = Query(..., min_length=1)):
    try:
        prompt = f"""Please generate Python code to complete the following task. Ensure all file operations stay within the /data directory and no deletions occur. Output only the code in a markdown code block. Task: {task}"""
        llm_response = call_llm(prompt)
        code = extract_code(llm_response)
        validate_code(code)
        
        code_path = Path("/tmp/generated_code.py")
        code_path.write_text(code)
        
        result = subprocess.run(
            ["python", str(code_path)],
            capture_output=True,
            text=True,
            timeout=15
        )
        
        if result.returncode != 0:
            error_msg = f"Execution error: {result.stderr}"
            logging.error(error_msg)
            raise HTTPException(status_code=500, detail=error_msg)
        
        return {"status": "success"}
    
    except HTTPException as he:
        raise he
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Task execution timed out.")
    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error.")

@app.get("/read")
async def read_file(path: str = Query(..., min_length=1)):
    try:
        resolved_path = resolve_path(path)
        if not resolved_path.exists():
            raise HTTPException(status_code=404)
        return resolved_path.read_text()
    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Read error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error.")

# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="0.0.0.0", port=8000)