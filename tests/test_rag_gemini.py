import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load .env to get the GEMINI_API_KEY
from dotenv import load_dotenv
load_dotenv()

from app.rag import RAGManager

def test_rag_flow():
    rag = RAGManager.get_instance()
    
    print("--- 1. Testing RAG Initialization ---")
    success = rag.initialize()
    if not success:
        print("❌ RAG Initialization Failed (Check if PDF exists and libs are installed)")
        return
    print("✅ RAG Initialization Successful")
    
    print("\n--- 2. Testing Logic Queries ---")
    queries = [
        "What are the rules for High Rise Buildings?",
        "Explain setback with a doctor analogy."
    ]
    
    for q in queries:
        print(f"Query: {q}")
        ans = rag.query(q)
        print(f"Answer:\n{ans}\n")
        print("-" * 40)

if __name__ == "__main__":
    test_rag_flow()
