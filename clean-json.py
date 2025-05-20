import os
import re
import json
from glob import glob

def clean_name(name):
    # Remove newlines and excessive whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    # Remove repeated names (e.g., 'Ben BortonBen Borton')
    match = re.match(r'^([A-Za-z .\-\'’]+)\1', name)
    if match:
        name = match.group(1).strip()
    # Remove trailing info like '• ...'
    name = re.split(r' • |\|', name)[0].strip()
    return name

def clean_string(s):
    s = re.sub(r'\s+', ' ', s)
    return s.strip()

def clean_dict(d):
    if isinstance(d, dict):
        for k, v in d.items():
            if k == 'name' and isinstance(v, str):
                d[k] = clean_name(v)
            elif isinstance(v, str):
                d[k] = clean_string(v)
            elif isinstance(v, dict) or isinstance(v, list):
                d[k] = clean_dict(v)
    elif isinstance(d, list):
        d = [clean_dict(i) for i in d]
    return d

def main():
    input_dir = 'posts/json'
    output_dir = 'posts/clean'
    os.makedirs(output_dir, exist_ok=True)
    for file in glob(os.path.join(input_dir, '*.json')):
        with open(file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        cleaned = clean_dict(data)
        out_path = os.path.join(output_dir, os.path.basename(file))
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(cleaned, f, ensure_ascii=False, indent=2)

if __name__ == '__main__':
    main()
