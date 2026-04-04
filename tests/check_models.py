import os
import requests
from dotenv import load_dotenv

load_dotenv('/Users/thrisha/BS_APP/buildsmart/.env')
api_key = os.environ.get("GEMINI_API_KEY")

if not api_key:
    print("API Key not found in .env. Please make sure GEMINI_API_KEY is set.")
    exit(1)

url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
try:
    response = requests.get(url)
    data = response.json()
    if 'models' in data:
        print("\n--- Available Gemini Models ---")
        for m in data['models']:
            if 'generateContent' in m.get('supportedGenerationMethods', []):
                name = m['name'].replace('models/', '')
                if 'gemini' in name.lower():
                    print(f"✅ {name}")
        print("-------------------------------")
    else:
        print(f"Error from API: {data}")
except Exception as e:
    print(f"Request failed: {e}")
